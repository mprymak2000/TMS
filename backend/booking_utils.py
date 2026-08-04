from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func
from sqlalchemy.orm import Session
from models import Booking, BookingSeries
from schemas import BookingResponse
from policy import get_cancel_action, get_reschedule_action, cancel_blocked_detail, reschedule_blocked_detail
from fastapi import HTTPException
     


def _occurrence_end(series: BookingSeries, start_local_date: date, tz: ZoneInfo) -> datetime:
    """Given a series and a candidate local start date, compute the occurrence's UTC end time."""
    end_date = start_local_date + timedelta(days=(series.end_day_of_week - series.start_day_of_week) % 7)
    return datetime.combine(end_date, series.end_time, tzinfo=tz).astimezone(UTC)


def _ensure_occurrence(series: BookingSeries, start_utc: datetime, db: Session, settings) -> Booking:
    """Ensure a specific occurrence of a series exists. Returns existing or newly created Booking."""
    booking = db.query(Booking).filter(Booking.series_id == series.id, Booking.start == start_utc).first()
    if booking:
        # if exists, return. no-op. idempotent
        return booking
    tz = ZoneInfo(settings.business_timezone)
    start_local = start_utc.astimezone(tz)
    if start_local.weekday() != series.start_day_of_week or start_local.time() != series.start_time:
        raise ValueError("Datetime does not match series schedule")
    earliest = db.query(func.min(Booking.start)).filter(Booking.series_id == series.id).scalar()
    if earliest is not None:
        earliest_tz = earliest if earliest.tzinfo else earliest.replace(tzinfo=UTC)
        if start_utc < earliest_tz:
            raise ValueError("Datetime is before this series' earliest occurrence")
    end_utc = _occurrence_end(series, start_local.date(), tz)
    new_booking = Booking(
        public_id=f"{series.public_id}:{int(start_utc.timestamp())}",
        series_id=series.id,
        tutor_id=series.tutor_id,
        event_type_id=series.event_type_id,
        student_id=series.student_id,
        student_first=series.student_first,
        student_last=series.student_last,

        student_email=series.student_email,
        student_phone=series.student_phone,
        parent_email=series.parent_email,
        parent_phone=series.parent_phone,
        google_event_id=series.google_event_id,
        start=start_utc,
        end=end_utc,
        status="confirmed",
        timezone=series.bookings[0].timezone if series.bookings else settings.business_timezone,
    )
    db.add(new_booking)
    db.flush()
    db.refresh(new_booking)
    return new_booking


def resolve_ref(ref: str, db: Session, settings) -> Booking:
    """
    Resolves a booking ref to a Booking row.

    Two ref formats:
      Plain public_id (standalone booking, or any already-materialized series occurrence —
        its public_id already equals the composite form below, set at creation time):
        e.g. f47ac10b-58cc-4372-a567-0e02b2c3d479
      Composite (not-yet-materialized series occurrence): {series.public_id}:{unix_timestamp}
        e.g. a1b2c3d4-...-uuid:1753952400
        unix timestamp = seconds since epoch (UTC). In JS: Math.floor(Date.now() / 1000)

    Tries a direct public_id lookup first — this alone covers standalone bookings and any
    occurrence that's already a real row. Only falls back to composite parsing (and
    materializing via _ensure_occurrence) when nothing matches, i.e. a genuinely virtual
    occurrence. Flushes but doesn't commit on materialization — caller owns the transaction.
    """
    booking = db.query(Booking).filter(Booking.public_id == ref).first()
    if booking:
        return booking

    if ":" not in ref:
        raise HTTPException(status_code=404, detail="Booking not found")

    series_public_id, _, ts_part = ref.partition(":")
    try:
        start_utc = datetime.fromtimestamp(int(ts_part), tz=UTC)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(status_code=400, detail="Invalid booking ref")

    series = db.query(BookingSeries).filter(BookingSeries.public_id == series_public_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Booking not found")
    if not series.is_active:
        raise HTTPException(status_code=400, detail="Booking series has been cancelled")
    if series.recur_until is not None:
        tz = ZoneInfo(settings.business_timezone)
        if start_utc.astimezone(tz).date() > series.recur_until:
            raise HTTPException(status_code=400, detail="Occurrence is past the end of this series")

    try:
        return _ensure_occurrence(series, start_utc, db, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _virtual_occurrences(series: BookingSeries, after: datetime, count: int, settings) -> list[BookingResponse]:
    """Generates up to `count` virtual occurrences for a series starting from `after`, skipping
    any date that already has a real Booking row. Stops early if recur_until is reached first.
    Never touches the DB.

    Advances one week at a time — this app's recurrence is always weekly today. If a variable
    interval (daily, every-N-weeks, monthly, etc.) is ever added to BookingSeries, this is the
    one place that needs to change.
    """
    tz = ZoneInfo(settings.business_timezone)
    existing_starts = {b.start for b in series.bookings}

    # Never generate virtual occurrences before the series' actual earliest real row — same
    # reasoning as _ensure_occurrence's lower-bound check. Without this, a series that starts
    # in the future would show phantom virtual occurrences dated before it actually begins.
    after_tz = after if after.tzinfo else after.replace(tzinfo=UTC)
    if existing_starts:
        earliest = min(s if s.tzinfo else s.replace(tzinfo=UTC) for s in existing_starts)
        after_tz = max(after_tz, earliest)

    # get datetime series occurrences in local time for DST safety, then advance weekly (should be same time every week)
    cursor_date = after_tz.astimezone(tz).date()
    days_until_next_occurrence = (series.start_day_of_week - cursor_date.weekday()) % 7
    cursor_date += timedelta(days=days_until_next_occurrence) # next occurrence of the series

    occurrences = []
    while len(occurrences) < count:
        # if series is finite, stop generating occurrences after recur_until
        if series.recur_until is not None and cursor_date > series.recur_until:
            break
        start_utc = datetime.combine(cursor_date, series.start_time, tzinfo=tz).astimezone(UTC) # next ocurrence local -> utc

        # some occurrences may already exist if acted on by user or background job, don't touch those
        if start_utc not in existing_starts:
            end_utc = _occurrence_end(series, cursor_date, tz)
            minutes_until = (start_utc - datetime.now(UTC)).total_seconds() / 60
            cancel_action = get_cancel_action(series.event_type, minutes_until)
            reschedule_action = get_reschedule_action(series.event_type, minutes_until)
            occurrences.append(
                BookingResponse(
                    public_id=f"{series.public_id}:{int(start_utc.timestamp())}",
                    series_public_id=series.public_id,
                    rescheduled_to_public_id=None,
                    tutor_id=series.tutor_id,
                    event_type_id=series.event_type_id,
                    student_id=series.student_id,
                    start=start_utc,
                    end=end_utc,
                    timezone=settings.business_timezone,
                    status="confirmed",
                    is_no_show=False,
                    google_event_id=series.google_event_id,
                    cancel_action=cancel_action,
                    cancel_blocked_reason=None if cancel_action == 'auto' else cancel_blocked_detail(series.event_type),
                    reschedule_action=reschedule_action,
                    reschedule_blocked_reason=None if reschedule_action == 'auto' else reschedule_blocked_detail(series.event_type),
                    student_first=series.student_first,
                    student_last=series.student_last,
                    student_email=series.student_email,
                    student_phone=series.student_phone,
                    parent_email=series.parent_email,
                    parent_phone=series.parent_phone,
                    request=None,
                )
            )
        cursor_date += timedelta(days=7)
    return occurrences