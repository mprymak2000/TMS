from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func
from sqlalchemy.orm import Session
from models import Booking, BookingSeries
from schemas import BookingResponse
from policy import get_cancel_action, get_reschedule_action
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
    occurrence that's already a materialized row. Only falls back to composite parsing (and
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


def _virtual_occurrences(
        series: BookingSeries,
        time_min: datetime | None,
        time_max: datetime | None,
        count: int, 
        settings
    ) -> list[BookingResponse]:
    """Generates up to `count` virtual occurrences for a series within [time_min, time_max],
     skipping any date that already has a materialized Booking row (in db). Stops early if recur_until or
     time_max is reached first. Never touches the DB.

     time_min/time_max mirror Google Calendar's timeMin/timeMax — both optional. Omitting time_min
     means "since the series actually started" (series.start_date); omitting time_max means
     unbounded — count (page * page_size from the caller) is what guarantees termination in that
     case, same role pageToken/maxResults plays for Google.

     Advances one week at a time — this app's recurrence is always weekly today. If a variable
     interval is ever added to BookingSeries, this is the one place that needs to change.
     """
    tz = ZoneInfo(settings.business_timezone)
    existing_starts = {b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC) for b in series.bookings} # existing materialized occurrences part of series, to skip when generating virtual occurrences

    # Need a FLOOR so no virtual occurrences are generated before the series actually started. 
    floor_date = series.start_date
    if time_min is not None:
        time_min_tz = time_min if time_min.tzinfo else time_min.replace(tzinfo=UTC)
        floor_date = max(floor_date, time_min_tz.astimezone(tz).date()) # first occ is in local timezone, convert from utc to local

    time_max_date = None
    if time_max is not None:
        time_max_tz = time_max if time_max.tzinfo else time_max.replace(tzinfo=UTC)
        time_max_date = time_max_tz.astimezone(tz).date()

    # point at the first occurrence in local time, then jump forward by one week (DST safe) and generate occurrence objects
    cursor_date = floor_date
    days_until_next_occurrence = (series.start_day_of_week - cursor_date.weekday()) % 7 # no-op if floor is the series first occurrence
    cursor_date += timedelta(days=days_until_next_occurrence) # next occurrence of the series

    occurrences = []
    while len(occurrences) < count:
        # if series is finite, stop generating occurrences after recur_until - can't have more occurrences
        if series.recur_until is not None and cursor_date > series.recur_until:
            break
        # if upper bound date is set, stop generating occurrences after time_max
        if time_max_date is not None and cursor_date > time_max_date:
            break
        start_utc = datetime.combine(cursor_date, series.start_time, tzinfo=tz).astimezone(UTC) # next ocurrence local -> utc

        # skip occurrences that were already materialized and acted on by user or background job
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
                    reschedule_action=reschedule_action,
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


def merge_occurrences(
    relevant_series: list[BookingSeries],
    materialized_query,
    time_min: datetime | None,
    time_max: datetime | None,
    page: int,
    page_size: int,
    settings,
) -> list[BookingResponse]:
    """Merge materialized (in db) + virtual occurrences across the given series within [time_min, time_max],
     paginate to one page. Shared by GET /bookings/ and
     GET /bookings/booking-series/{id}/occurrences.

     time_min/time_max optional — omitting either
     means unbounded on that side. page/page_size remains a 'local' termination mechanism for an
     indefinite series when time_max is omitted and can be recalled indefinitely;
     time_max, when given, is an 'absolute' stop.
     """
    materialized_bookings_query = materialized_query.filter(Booking.status == "confirmed")
    if time_min is not None:
        materialized_bookings_query = materialized_bookings_query.filter(Booking.start >= time_min)
    if time_max is not None:
        materialized_bookings_query = materialized_bookings_query.filter(Booking.start <= time_max)
    materialized_bookings = materialized_bookings_query.all()
    
    needed_total = page * page_size
    virtual = []
    for series in relevant_series:
        virtual.extend(_virtual_occurrences(series, time_min, time_max, needed_total, settings))

    def _sort_key(b):
        return b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC)

    merged = sorted([*materialized_bookings, *virtual], key=_sort_key)
    start = (page - 1) * page_size
    return [BookingResponse.model_validate(b) for b in merged[start:start + page_size]]