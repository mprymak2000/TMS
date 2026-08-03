from uuid import uuid4
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from models import Booking, BookingSeries
from fastapi import HTTPException
     


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
    end_date = start_local.date() + timedelta(days=(series.end_day_of_week - series.start_day_of_week) % 7)
    end_utc = datetime.combine(end_date, series.end_time, tzinfo=tz).astimezone(UTC)
    new_booking = Booking(
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
        manage_token=str(uuid4()),
        timezone=series.bookings[0].timezone if series.bookings else settings.business_timezone,
        public_id=f"{series.public_id}:{int(start_utc.timestamp())}",
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


# TODO: not called anywhere yet — the /manage-occurrence/{token} routes still do a plain
# manage_token lookup, so this currently does nothing. Kept for when email-based manage links
# for not-yet-materialized occurrences are wired up (customers won't get an email for an
# occurrence until it exists, so this isn't urgent, but the composite-token resolution will
# be needed once reminder/confirmation emails reference future occurrences directly).
def _ensure_occurrence_by_token(token: str, db: Session, settings) -> Booking:
    """
    Resolves an occurrence token to a Booking, flushing (not committing) if a new row is created.

    Two token formats:
      Composite (virtual occurrence): series:{series_manage_token}:t:{unix_timestamp_seconds}
        e.g. series:a1b2c3d4-...-uuid:t:1753952400
        unix timestamp = seconds since epoch (UTC). In JS: Math.floor(Date.now() / 1000)
      Real (materialized occurrence): plain UUID manage_token
        e.g. f47ac10b-58cc-4372-a567-0e02b2c3d479

    settings is passed through to _ensure_occurrence which needs settings.business_timezone
    to convert start_utc to local time for weekday/time validation.

    Caller is responsible for committing or rolling back.
    For GET endpoints: db.commit() after this call to persist any new row.
    For action endpoints (cancel/reschedule): the action's own db.commit() covers it atomically.
    """
    if token.startswith("series:"):
        parts = token.split(":")
        # Expected: ["series", "<uuid>", "t", "<unix_timestamp>"]
        if len(parts) != 4 or parts[2] != "t":
            raise HTTPException(status_code=400, detail="Invalid occurrence token format")
        series_token = parts[1]
        try:
            start_datetime_utc = datetime.fromtimestamp(int(parts[3]), tz=UTC)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid occurrence token timestamp")

        series = db.query(BookingSeries).filter(BookingSeries.manage_token == series_token).first()
        if not series:
            raise HTTPException(status_code=404, detail="Booking series not found")
        if not series.is_active:
            raise HTTPException(status_code=400, detail="Booking series has been cancelled and cannot be managed")

        try:
            return _ensure_occurrence(series, start_datetime_utc, db, settings)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    else:
        # Real token format: plain UUID (manage_token on Booking)
        booking = db.query(Booking).filter(Booking.manage_token == token).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        return booking
        