# Occurrence generation strategy:
# - Finite series (Mode A/B): all occurrences generated at create time. recur_until is set.
# - Indefinite series (Mode C): only occurrence 1 generated at create time. recur_until=None.
#   Daily Procrastinate job (tasks.extend_series) finds these and generates the next occurrence.
#   _ensure_occurrence (booking_utils.py) handles on-demand generation for both paths.
# Debug endpoint planned: POST /bookings/series/{series_id}/ensure-occurrence?date=YYYY-MM-DD

import logging
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from booking_utils import resolve_ref, merge_occurrences
from database import get_db, get_settings
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from gcal import SCOPES, get_calendar_service
from models import Booking, BookingRequest, BookingSeries, EventType, Tutor
from policy import (
    get_cancel_action,
    get_reschedule_action,
)
from schemas import (
    BookingCreate,
    BookingRequestResponse,
    BookingReschedule,
    BookingResponse,
    BookingSeriesResponse,
    BookingUpdate,
)
from sqlalchemy.orm import Session

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
PAGE_SIZE = 10

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.get("/booking-series", response_model=list[BookingSeriesResponse])
def get_booking_series(
    email: str | None = Query(default=None),
    tutor_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Bare series list — no embedded occurrences. Expanding a series row on the frontend
    calls GET /booking-series/{id}/occurrences separately, the same way GET / does it
    internally — one shared resolution path (merge_occurrences) for all occurrence reads,
    regardless of whether the caller wants everyone's occurrences or just one series'."""
    q = db.query(BookingSeries).filter(BookingSeries.is_active == True)
    if email:
        q = q.filter((BookingSeries.student_email == email) | (BookingSeries.parent_email == email))
    if tutor_id:
        q = q.filter(BookingSeries.tutor_id == tutor_id)
    results = []
    for series in q.all():
        resp = BookingSeriesResponse.model_validate(series)
        resp.bookings = []  # unused here — occurrences are always fetched separately, paginated
        results.append(resp)
    return results


@router.get("/booking-series/{id}/occurrences", response_model=list[BookingResponse])
def get_booking_series_occurrences(
    id: str,
    response: Response,
    time_min: datetime | None = Query(default=None),
    time_max: datetime | None = Query(default=None),
    include_cancelled: bool = Query(default=False),
    order: str = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=PAGE_SIZE, ge=1, le=500),
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Returns a paginated list of all occurrences (materialized + virtual) for a given series.
    page_size defaults to PAGE_SIZE (10) but callers can request a smaller/larger page — same
    page_size override pattern as GET /bookings/ (list_bookings)."""
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Booking series not found")

    materialized_query = db.query(Booking).filter(Booking.series_id == series.id) # materialized occurrences only part of a series
    items, total = merge_occurrences([series], materialized_query, time_min, time_max, page, page_size, settings, include_cancelled, order)
    if total is not None:
        response.headers["X-Total-Count"] = str(total)
    return items

@router.get("/", response_model=list[BookingResponse])
def list_bookings(
    response: Response,
    email: str | None = Query(default=None),
    tutor_ids: list[int] = Query(default=[]),
    event_type_ids: list[int] = Query(default=[]),
    time_min: datetime | None = Query(default=None),
    time_max: datetime | None = Query(default=None),
    include_cancelled: bool = Query(default=False),
    order: str = Query(default="asc"),
    pending_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=PAGE_SIZE, ge=1, le=500),
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Returns a paginated, flat list of all bookings (materialized + virtual occurrences).
    Optionally filtered to a student/parent email, one or more tutor_ids, and/or one or more
    event_type_ids. Optionally filtered to only pending requests.
    Virtual ocurrences (from series rules) are generated in memory and merged with real rows, then the combined list is paginated.
    Min and max optional filters are used to scope the time window for ocurrences. The default floor is to start from the first occurrence,
    while max is dynamically calculated based on the number of pages requested (pagination).

    page_size defaults to PAGE_SIZE (10, real pagination — used by Range) but callers with a
    naturally-bounded window (Day/Week/Month) can request a larger one — same idea as Google
    Calendar's events.list maxResults (default/typical 250): a single practitioner's day/week/
    month is never going to have hundreds of bookings, so there's no real "page 2" to speak of;
    a generous cap is just a safety net, not a genuine pagination boundary. Capped at 500 to
    bound worst-case cost on an unbounded (no time_max) query, where page_size directly controls
    how deep _virtual_occurrences walks.

    Returned list represents a real timeline of all bookings."""
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    if pending_only:
        # a virtual occurrence can never have a pending BookingRequest (nothing to request
        # approval on for a row that doesn't exist yet) — real rows only, no virtual merge.
        query = db.query(Booking).filter(Booking.request.has(BookingRequest.status == 'pending'))
        if email:
            query = query.filter((Booking.student_email == email) | (Booking.parent_email == email))
        if tutor_ids:
            query = query.filter(Booking.tutor_id.in_(tutor_ids))
        if event_type_ids:
            query = query.filter(Booking.event_type_id.in_(event_type_ids))
        return query.order_by(Booking.start.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # Get booking rows that already exist in db (materialized occurrences). Filter if needed.
    materialized_query = db.query(Booking)
    if email:
        materialized_query = materialized_query.filter((Booking.student_email == email) | (Booking.parent_email == email))
    if tutor_ids:
        materialized_query = materialized_query.filter(Booking.tutor_id.in_(tutor_ids))
    if event_type_ids:
        materialized_query = materialized_query.filter(Booking.event_type_id.in_(event_type_ids))

    # Get series rows in db (BookingSeries - rules). Filter if needed.
    series_query = db.query(BookingSeries).filter(BookingSeries.is_active == True)
    if email:
        series_query = series_query.filter((BookingSeries.student_email == email) | (BookingSeries.parent_email == email))
    if tutor_ids:
        series_query = series_query.filter(BookingSeries.tutor_id.in_(tutor_ids))
    if event_type_ids:
        series_query = series_query.filter(BookingSeries.event_type_id.in_(event_type_ids))

    # Merge materialized + resolved virtual occurrences across all series, then paginate the combined list.
    items, total = merge_occurrences(series_query.all(), materialized_query, time_min, time_max, page, page_size, settings, include_cancelled, order)
    if total is not None:
        response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/{ref}", response_model=BookingResponse)
def get_booking(ref: str, db: Session = Depends(get_db)):
    # Deliberately does NOT use resolve_ref — reads must never materialize a row.
    # A ref pointing at a not-yet-materialized virtual occurrence simply 404s here;
    # browsing virtual occurrences is the job of the (future) paginated list endpoint,
    # which builds them in memory without writing to the DB.
    db_booking = db.query(Booking).filter(Booking.public_id == ref).first()
    if not db_booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return db_booking


@router.post("/", response_model=BookingResponse, status_code=201)
def create_booking(booking_in: BookingCreate, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_tutor = db.query(Tutor).filter(Tutor.id == booking_in.tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    if not db_tutor.calendar_id:
        raise HTTPException(status_code=400, detail="Tutor must have a calendar ID")
    db_event_type = db.query(EventType).filter(EventType.id == booking_in.event_type_id).first()
    if not db_event_type:
        raise HTTPException(status_code=404, detail="Event type not found")
    BUSINESS_TZ = ZoneInfo(settings.business_timezone)
    # Conflict check — must run before touching Google Calendar so a rejection is cheap.
    # The loop walks every occurrence that would be created and queries the DB for any
    # confirmed booking that overlaps it. One hit → reject the whole request.
    _duration = booking_in.end - booking_in.start

    if db_event_type.expires_on is not None and booking_in.start.date() > db_event_type.expires_on:
        raise HTTPException(status_code=400, detail="Booking start is after this event type's expiry date")

    # _gen_through: last occurrence date to conflict-check before accepting the booking.
    if db_event_type.recurring:
        if db_event_type.expires_on is not None:
            # Mode A: all series of this event type end on a fixed calendar date
            _gen_through = db_event_type.expires_on
        elif db_event_type.booker_can_set_recur_until and booking_in.recur_until is not None:
            # Mode B/C variant: booker explicitly chose an end date for the series on the booking form
            _gen_through = booking_in.recur_until
        elif db_event_type.recur_weeks is not None:
            # Mode B: recur_weeks = total number of occurrences; last is at start + (N-1) weeks
            _gen_through = booking_in.start.date() + timedelta(weeks=db_event_type.recur_weeks - 1)
        else:
            # Mode C (indefinite): only check occurrence 1 — slot picker handles future conflict detection
            _gen_through = booking_in.start.date()
    else:
        # Standalone: setting _gen_through = start.date() makes the loop run exactly once,
        # checking only the single requested slot, without needing a separate code path.
        _gen_through = booking_in.start.date()

    occ = booking_in.start
    while occ.date() <= _gen_through:
        if db.query(Booking).filter(
            Booking.tutor_id == booking_in.tutor_id,
            Booking.status == "confirmed",
            Booking.start < occ + _duration,   # standard half-open interval overlap check:
            Booking.end > occ,                  # [occ, occ+duration) overlaps [start, end) iff start < occ+dur AND end > occ
        ).first():
            raise HTTPException(status_code=409, detail="One or more occurrences conflict with an existing booking")
        occ = (occ.astimezone(BUSINESS_TZ) + timedelta(days=7)).astimezone(UTC)

    # Compute recur_until_date before building the calendar event so we can include UNTIL in the RRULE
    recur_until_date = None
    if db_event_type.recurring:
        if db_event_type.expires_on is not None:
            recur_until_date = db_event_type.expires_on
        elif db_event_type.booker_can_set_recur_until and booking_in.recur_until is not None:
            recur_until_date = booking_in.recur_until
        elif db_event_type.recur_weeks is not None:
            recur_until_date = booking_in.start.date() + timedelta(weeks=db_event_type.recur_weeks - 1)

    new_public_id = str(uuid4())
    manage_path = "manage-series" if db_event_type.recurring else "manage-occurrence"
    manage_url = f"{FRONTEND_URL}/{manage_path}/{new_public_id}"
    description_parts = [p for p in [db_event_type.description, f"Manage your booking: {manage_url}"] if p]
    new_event = {
            "summary": f"{db_event_type.name}: {booking_in.student_first} and {db_tutor.first_name}",
            "description": "\n\n".join(description_parts),
            "start": {"dateTime": booking_in.start.isoformat(), "timeZone": settings.business_timezone},
            "end": {"dateTime": booking_in.end.isoformat(), "timeZone": settings.business_timezone},
        }
    if db_event_type.recurring:
        rrule = "RRULE:FREQ=WEEKLY"
        if recur_until_date is not None:
            rrule += f";UNTIL={recur_until_date.strftime('%Y%m%d')}"
        new_event["recurrence"] = [rrule]

    service = get_calendar_service(SCOPES)
    try:
        google_event = service.events().insert(calendarId=db_tutor.calendar_id, body=new_event).execute()
    except Exception as e:
        logging.error(f"Failed to create calendar event: {e}")
        raise HTTPException(status_code=500, detail="Failed to create calendar event") from e

    try:
        if db_event_type.recurring:
            local_start = booking_in.start.astimezone(BUSINESS_TZ)
            local_end = booking_in.end.astimezone(BUSINESS_TZ)
            series = BookingSeries(
                public_id=new_public_id,
                **booking_in.model_dump(exclude={"start", "end", "timezone", "recur_until"}),
                start_date=local_start.date(),
                start_day_of_week=local_start.weekday(),
                end_day_of_week=local_end.weekday(),
                start_time=local_start.time(),
                end_time=local_end.time(),
                recur_until=recur_until_date,
                google_event_id=google_event["id"],
            )
            db.add(series)
            db.flush()

            base_fields = booking_in.model_dump(exclude={"recur_until", "start", "end"})
            occ_start = booking_in.start
            first_booking = None
            while occ_start.date() <= _gen_through:
                booking = Booking(
                    public_id=f"{series.public_id}:{int(occ_start.timestamp())}",
                    **base_fields,
                    series_id=series.id,
                    google_event_id=google_event["id"],
                    start=occ_start,
                    end=occ_start + _duration,
                    status="confirmed",
                )
                db.add(booking)
                if first_booking is None:
                    first_booking = booking
                occ_start = (occ_start.astimezone(BUSINESS_TZ) + timedelta(days=7)).astimezone(UTC)
            db.commit()
            db.refresh(first_booking)
            return first_booking

        new_booking = Booking(
            public_id=new_public_id,
            **booking_in.model_dump(exclude={"recur_until"}),
            google_event_id=google_event["id"],
            status="confirmed",
        )
        db.add(new_booking)
        db.commit()
        db.refresh(new_booking)
        return new_booking
    except Exception:
        try:
            service.events().delete(calendarId=db_tutor.calendar_id, eventId=google_event["id"]).execute()
        except Exception:
            logging.warning(f"WARNING: Calendar event was added to calendar, but DB add FAILED and calendar event cleanup ALSO FAILED, manual cleanup required for event with id {google_event['id']} in calendar {db_tutor.calendar_id}")
        raise HTTPException(status_code=500, detail="Booking failed, calendar event rolled back")


def _reschedule_booking(db_booking: Booking, booking_in: BookingReschedule, db: Session, service) -> Booking:
    """Reschedule saga — calendar patch/replace + new Booking row + soft-delete original + compensation.
    Caller is responsible for all policy checks (status, notice window, is-rescheduling-allowed)
    before calling this. Helper assumes the booking is valid to reschedule."""
    db_tutor = db.query(Tutor).filter(Tutor.id == booking_in.tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    if not db_tutor.calendar_id:
        raise HTTPException(status_code=400, detail="Tutor must have a calendar ID")

    is_series = db_booking.series_id is not None
    # Save all relationship-accessed values upfront before any op that could expire the session
    original_start = db_booking.start
    original_end = db_booking.end
    booking_id = db_booking.id
    old_calendar_id = db_booking.tutor.calendar_id
    old_google_event_id = db_booking.google_event_id
    old_series_google_event_id = db_booking.series.google_event_id if is_series else None
    series_public_id = db_booking.series.public_id if is_series else None
    event_type_name = db_booking.event_type.name
    event_type_description = db_booking.event_type.description

    # --- Step 1: Calendar operation ---
    # Series: patch the specific RRULE instance (creates a Google Calendar exception, no new event)
    # Standalone: create a new calendar event and delete the old one later
    new_public_id = f"{series_public_id}:{int(booking_in.start.timestamp())}" if is_series else str(uuid4())
    if is_series:
        try:
            is_exception = old_google_event_id != old_series_google_event_id
            if is_exception:
                # Already an exception event from a prior reschedule — its own ID is stored directly, use it
                instance_id = old_google_event_id
            else:
                # Normal occurrence — master RRULE ID stored; look up the specific instance by time window
                instances = service.events().instances(
                    calendarId=old_calendar_id,
                    eventId=old_series_google_event_id,
                    timeMin=(original_start - timedelta(seconds=1)).isoformat(),
                    timeMax=(original_end + timedelta(seconds=1)).isoformat(),
                ).execute()
                if not instances.get("items"):
                    raise HTTPException(status_code=400, detail="Calendar instance not found for this booking, cannot reschedule")
                instance_id = instances["items"][0]["id"]
            service.events().patch(calendarId=old_calendar_id, eventId=instance_id, body={
                "start": {"dateTime": booking_in.start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": booking_in.end.isoformat(), "timeZone": "UTC"},
            }).execute()
            new_google_event_id = instance_id
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to reschedule calendar event") from e
    else:
        #todo: allow custom event name templates per event type using dynamic tags e.g. "{student_first} {student_last} - {event_type}"
        manage_url = f"{FRONTEND_URL}/manage-occurrence/{new_public_id}"
        description_parts = [p for p in [event_type_description, f"Manage your booking: {manage_url}"] if p]
        new_event = {
            "summary": f"{event_type_name}: {db_booking.student_first} and {db_tutor.first_name}",
            "description": "\n\n".join(description_parts),
            "start": {"dateTime": booking_in.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": booking_in.end.isoformat(), "timeZone": "UTC"},
        }
        try:
            google_event = service.events().insert(calendarId=db_tutor.calendar_id, body=new_event).execute()
            new_google_event_id = google_event["id"]
        except Exception as e:
            logging.error(f"Failed to create calendar event: {e}")
            raise HTTPException(status_code=500, detail="Failed to create calendar event") from e

    # --- Step 2: Insert new booking row ---
    # Inherits series_id, student info, event type from original; gets new time and google_event_id
    updated_booking = {
        "public_id": new_public_id,
        **booking_in.model_dump(),
        "series_id": db_booking.series_id,
        "google_event_id": new_google_event_id,
        "status": "confirmed",
        "student_id": db_booking.student_id,
        "event_type_id": db_booking.event_type_id,
        "student_first": db_booking.student_first,
        "student_last": db_booking.student_last,
        "student_email": db_booking.student_email,
        "student_phone": db_booking.student_phone,
        "parent_email": db_booking.parent_email,
        "parent_phone": db_booking.parent_phone,
    }
    new_booking = Booking(**updated_booking)
    db.add(new_booking)
    try:
        db.flush()
    except Exception as e:
        # DB failed after calendar was already modified — compensate using pre-saved values (session may be expired)
        try:
            if is_series:
                service.events().patch(calendarId=old_calendar_id, eventId=instance_id, body={
                    "start": {"dateTime": original_start.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": original_end.isoformat(), "timeZone": "UTC"},
                }).execute()
            else:
                service.events().delete(calendarId=db_tutor.calendar_id, eventId=new_google_event_id).execute()
        except Exception:
            logging.warning(f"WARNING: DB flush failed and calendar compensation also failed, manual cleanup required for event {new_google_event_id} in calendar {old_calendar_id}")
        raise HTTPException(status_code=500, detail="Failed to create booking record") from e

    # --- Step 3: Soft-delete original, then finalize ---
    db_booking.status = "rescheduled"
    db_booking.rescheduled_to = new_booking.id

    # Standalone only: delete the old calendar event (series instance was already replaced by the patch above)
    if not is_series:
        try:
            service.events().delete(calendarId=old_calendar_id, eventId=old_google_event_id).execute()
        except Exception as e:
            db.rollback()
            try:
                # new event exists on calendar but DB was rolled back — clean it up
                service.events().delete(calendarId=db_tutor.calendar_id, eventId=new_google_event_id).execute()
            except Exception:
                logging.warning(f"WARNING: Failed to delete original calendar event. DB rolled back but new event {new_google_event_id} remains on calendar {db_tutor.calendar_id}. Manual cleanup required.")
            raise HTTPException(status_code=500, detail="Failed to delete original calendar event") from e

    try:
        db.commit()
    except Exception as e:
        # Calendar already updated — compensate
        try:
            if is_series:
                # Patch instance back to original time
                service.events().patch(calendarId=old_calendar_id, eventId=instance_id, body={
                    "start": {"dateTime": original_start.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": original_end.isoformat(), "timeZone": "UTC"},
                }).execute()
            else:
                # Delete the new event, then restore the old one — Google soft-deletes so the
                # original event ID is still valid and can be patched back to confirmed
                service.events().delete(calendarId=db_tutor.calendar_id, eventId=new_google_event_id).execute()
                service.events().patch(calendarId=old_calendar_id, eventId=old_google_event_id, body={
                    "status": "confirmed",
                    "start": {"dateTime": original_start.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": original_end.isoformat(), "timeZone": "UTC"},
                }).execute()
        except Exception:
            logging.warning(f"WARNING: DB commit failed and calendar compensation also failed for reschedule of booking {booking_id}. Manual cleanup required for event {new_google_event_id}.")
        raise HTTPException(status_code=500, detail="Failed to finalize reschedule") from e
    db.refresh(new_booking)
    return new_booking


@router.post("/{ref}/reschedule", response_model=BookingResponse)
def reschedule_booking(ref: str, booking_in: BookingReschedule, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_booking = resolve_ref(ref, db, settings)
    # Admin path: only the booking's own state is enforced (must still be confirmed) — event-type
    # policy (reschedule_mode) and the past-time notice-window floor are booker-facing rules and
    # deliberately don't apply to admin, who needs to override both (e.g. waiving a no-show fee).
    if db_booking.status != "confirmed":
        raise HTTPException(status_code=400, detail="Only confirmed bookings can be rescheduled")
    service = get_calendar_service(SCOPES)
    return _reschedule_booking(db_booking, booking_in, db, service)


@router.put("/booking-series/{id}", response_model=BookingSeriesResponse)
def update_booking_series(id: str, booking_in: BookingReschedule, db: Session = Depends(get_db), settings=Depends(get_settings)):
    # Admin path: only the series' own state is enforced (must still be active) — no
    # notice-window/policy check, same reasoning as the occurrence-level admin routes above.
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    if not db_series.is_active:
        raise HTTPException(status_code=400, detail="Booking series is not active")
    service = get_calendar_service(SCOPES)
    return _reschedule_series(db_series, booking_in, db, service, settings)


""" Change contact info for booking """
@router.put("/{ref}", response_model=BookingResponse)
def update_booking(ref: str, booking_in: BookingUpdate, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_booking = resolve_ref(ref, db, settings)
    for key, value in booking_in.model_dump().items():
        setattr(db_booking, key, value)
    try:
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to update booking") from e
    db.refresh(db_booking)
    return db_booking


@router.delete("/{ref}/permanent", status_code=204)
def permanently_delete_booking(ref: str, cascade: bool = False, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_booking = resolve_ref(ref, db, settings)

    # A rescheduled booking has a predecessor chain pointing to it via rescheduled_to FK.
    # First call (cascade=False): return 409 so the frontend can show a confirmation modal.
    # Second call (cascade=True): sent only after the user explicitly confirms — walks and deletes all predecessors.
    # Note: rescheduled_to is an internal integer FK (not public_id), so we match against
    # db_booking.id (the internal PK) here, not the public_id from the path param.
    immediate_predecessor = db.query(Booking).filter(Booking.rescheduled_to == db_booking.id).first()
    if immediate_predecessor and not cascade:
        raise HTTPException(status_code=409, detail="This booking has a rescheduled predecessor.")

    predecessors = []
    if cascade:
        current = immediate_predecessor
        while current is not None:
            predecessors.append(current)
            current = db.query(Booking).filter(Booking.rescheduled_to == current.id).first()

    # Save all relationship-accessed values upfront before any op that could expire the session
    calendar_id = db_booking.tutor.calendar_id
    booking_start = db_booking.start
    booking_end = db_booking.end
    booking_google_event_id = db_booking.google_event_id
    series_google_event_id = db_booking.series.google_event_id if db_booking.series_id is not None else None
    deleted_instance_id = None

    if calendar_id and booking_google_event_id:
        try:
            service = get_calendar_service(SCOPES)
            if db_booking.series_id is None:
                # Standalone: google_event_id is the actual event — delete directly
                deleted_instance_id = booking_google_event_id
                service.events().delete(calendarId=calendar_id, eventId=deleted_instance_id).execute()
            elif booking_google_event_id != series_google_event_id:
                # Exception event (occurrence was previously rescheduled): google_event_id is the
                # exception's own ID — delete it directly without going through instances()
                deleted_instance_id = booking_google_event_id
                service.events().delete(calendarId=calendar_id, eventId=deleted_instance_id).execute()
            else:
                # Normal series occurrence: google_event_id is the master RRULE ID.
                # Must fetch the specific instance by time window and delete only that instance.
                instances = service.events().instances(
                    calendarId=calendar_id,
                    eventId=series_google_event_id,
                    timeMin=(booking_start - timedelta(seconds=1)).isoformat(),
                    timeMax=(booking_end + timedelta(seconds=1)).isoformat(),
                ).execute()
                if instances.get("items"):
                    deleted_instance_id = instances["items"][0]["id"]
                    service.events().delete(calendarId=calendar_id, eventId=deleted_instance_id).execute()
        except Exception as e:
            logging.warning(f"Permanent delete: calendar event {booking_google_event_id} could not be deleted: {e}")

    for pred in predecessors:
        db.delete(pred)
    db.delete(db_booking)
    try:
        db.commit()
    except Exception as e:
        if deleted_instance_id and calendar_id:
            try:
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=deleted_instance_id,
                    body={
                        "status": "confirmed",
                        "start": {"dateTime": booking_start.isoformat(), "timeZone": "UTC"},
                        "end": {"dateTime": booking_end.isoformat(), "timeZone": "UTC"},
                    },
                ).execute()
            except Exception as comp_err:
                logging.warning(f"WARNING: DB commit failed and calendar compensation also failed for permanent delete of booking {ref}: {comp_err}")
        raise HTTPException(status_code=500, detail="Failed to permanently delete booking") from e
    return Response(status_code=204)

def _batch_delete_instances(service, calendar_id: str, instance_ids: list[str], series_id: int) -> None:
    """Delete calendar event instances via batched requests instead of one HTTP round-trip per
    instance — an indefinite series with no timeMax on the instances() lookup can return up to
    250 future instances, and 250 sequential delete() calls at ~0.3-0.5s each can take minutes.
    Google Calendar API caps batch size at 50 sub-requests, so chunk into batches of that size."""
    BATCH_SIZE = 50

    def _on_response(request_id, response, exception):
        if exception is not None:
            logging.warning(f"Failed to delete instance {request_id} for series {series_id}: {exception}")

    for i in range(0, len(instance_ids), BATCH_SIZE):
        chunk = instance_ids[i:i + BATCH_SIZE]
        batch = service.new_batch_http_request(callback=_on_response)
        for inst_id in chunk:
            batch.add(service.events().delete(calendarId=calendar_id, eventId=inst_id), request_id=inst_id)
        batch.execute()


def _cancel_series(db_series: BookingSeries, db: Session, service) -> BookingSeries:
    """Cancel series saga — truncates RRULE to today, deletes future occurrence rows, soft-deletes series.
    Caller is responsible for all policy checks (is_active, etc.) before calling this."""
    calendar_id = db_series.tutor.calendar_id
    event_id = db_series.google_event_id
    today_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    old_rrule = "RRULE:FREQ=WEEKLY"
    if db_series.recur_until:
        old_rrule += f";UNTIL={db_series.recur_until.strftime('%Y%m%dT235959Z')}"
    # Fetch all future instances upfront — needed for both deletion and compensation on DB failure.
    # Google soft-deletes instances so they can be restored by patching status back to "confirmed".
    future_instances = [
        (i["id"], i["start"], i["end"])
        for i in service.events().instances(
            calendarId=calendar_id,
            eventId=event_id,
            timeMin=datetime.now(UTC).isoformat(),
        ).execute().get("items", [])
    ]
    _batch_delete_instances(service, calendar_id, [inst_id for inst_id, _, _ in future_instances], db_series.id)
    try:
        service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={"recurrence": [f"RRULE:FREQ=WEEKLY;UNTIL={today_str}"]}
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to cancel future instances of the series on calendar") from e
    db.query(Booking).filter(Booking.series_id == db_series.id, Booking.start >= datetime.now(UTC)).delete(synchronize_session=False)
    db_series.is_active = False
    try:
        db.commit()
    except Exception as e:
        try:
            service.events().patch(calendarId=calendar_id, eventId=event_id, body={"recurrence": [old_rrule]}).execute()
        except Exception:
            logging.warning(f"WARNING: DB commit failed and RRULE restoration also failed for series {db_series.id}. Manual cleanup required for event {event_id}.")
        for inst_id, inst_start, inst_end in future_instances:
            try:
                service.events().patch(calendarId=calendar_id, eventId=inst_id, body={
                    "status": "confirmed",
                    "start": inst_start,
                    "end": inst_end,
                }).execute()
            except Exception:
                logging.warning(f"WARNING: DB commit failed and instance {inst_id} could not be restored for series {db_series.id}.")
        raise HTTPException(status_code=500, detail="Failed to cancel series") from e
    return db_series


def _reschedule_series(db_series: BookingSeries, booking_in: 
    BookingReschedule, db: Session, service, settings) -> BookingSeries:
    """Reschedule series saga — truncates old RRULE, creates new RRULE event on (possibly new) tutor's calendar,
    drops future occurrence rows, regenerates from the new time. Caller owns is_active check.
    Helper fetches and validates the new tutor and event type since they're needed for the calendar op."""
    db_tutor = db.query(Tutor).filter(Tutor.id == booking_in.tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    if not db_tutor.calendar_id:
        raise HTTPException(status_code=400, detail="Tutor must have a calendar ID")
    db_event_type = db.query(EventType).filter(EventType.id == db_series.event_type_id).first()
    if not db_event_type:
        raise HTTPException(status_code=404, detail="Event type not found")
    BUSINESS_TZ = ZoneInfo(settings.business_timezone)

    today_str = datetime.now(UTC).strftime("%Y%m%d")
    old_calendar_id = db_series.tutor.calendar_id
    old_google_event_id = db_series.google_event_id
    old_rrule = "RRULE:FREQ=WEEKLY"
    if db_series.recur_until:
        old_rrule += f";UNTIL={db_series.recur_until.strftime('%Y%m%d')}"
    # Save future exceptions upfront — Google doesn't auto-remove them when RRULE is truncated
    future_exceptions = [
        (b.google_event_id, b.start, b.end)
        for b in db.query(Booking).filter(
            Booking.series_id == db_series.id,
            Booking.start >= datetime.now(UTC),
            Booking.google_event_id != old_google_event_id,
        ).all()
    ]
    for exc_event_id, _, _ in future_exceptions:
        try:
            service.events().delete(calendarId=old_calendar_id, eventId=exc_event_id).execute()
        except Exception as e:
            logging.warning(f"Failed to delete exception event {exc_event_id} for series {db_series.id}: {e}")

    # Step 1: truncate old RRULE so past occurrences remain, future are cut off
    try:
        service.events().patch(
            calendarId=old_calendar_id,
            eventId=old_google_event_id,
            body={"recurrence": [f"RRULE:FREQ=WEEKLY;UNTIL={today_str}"]}
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to truncate old calendar series") from e

    # Step 2: create new RRULE event on new tutor's calendar
    rrule = "RRULE:FREQ=WEEKLY"
    if db_series.recur_until:
        rrule += f";UNTIL={db_series.recur_until.strftime('%Y%m%d')}"
    series_manage_url = f"{FRONTEND_URL}/manage-series/{db_series.public_id}"
    series_description_parts = [p for p in [db_event_type.description, f"Manage your booking: {series_manage_url}"] if p]
    new_event = {
        "summary": f"{db_event_type.name}: {db_series.student_first} and {db_tutor.first_name}",
        "description": "\n\n".join(series_description_parts),
        "start": {"dateTime": booking_in.start.isoformat(), "timeZone": settings.business_timezone},
        "end": {"dateTime": booking_in.end.isoformat(), "timeZone": settings.business_timezone},
        "recurrence": [rrule],
    }
    try:
        new_google_event = service.events().insert(calendarId=db_tutor.calendar_id, body=new_event).execute()
    except Exception as e:
        try:
            service.events().patch(calendarId=old_calendar_id, eventId=old_google_event_id, body={"recurrence": [old_rrule]}).execute()
        except Exception:
            logging.warning(f"WARNING: Old RRULE truncated and new event creation failed for series {db_series.id}. Compensation also failed. Manual cleanup required for event {old_google_event_id}.")
        for exc_event_id, exc_start, exc_end in future_exceptions:
            try:
                service.events().patch(calendarId=old_calendar_id, eventId=exc_event_id, body={
                    "status": "confirmed",
                    "start": {"dateTime": exc_start.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": exc_end.isoformat(), "timeZone": "UTC"},
                }).execute()
            except Exception:
                logging.warning(f"WARNING: Exception event {exc_event_id} could not be restored after new event creation failed for series {db_series.id}.")
        logging.error(f"Failed to create calendar event: {e}")
        raise HTTPException(status_code=500, detail="Failed to create new calendar series") from e

    # Step 3: drop future occurrence rows, update series metadata, regenerate occurrence 1
    db.query(Booking).filter(
        Booking.series_id == db_series.id,
        Booking.start >= datetime.now(UTC)
    ).delete(synchronize_session=False)
    local_start = booking_in.start.astimezone(BUSINESS_TZ)
    local_end = booking_in.end.astimezone(BUSINESS_TZ)
    db_series.tutor_id = booking_in.tutor_id
    db_series.start_date = local_start.date()
    db_series.start_day_of_week = local_start.weekday()
    db_series.end_day_of_week = local_end.weekday()
    db_series.start_time = local_start.time()
    db_series.end_time = local_end.time()
    db_series.google_event_id = new_google_event["id"]
    db.add(Booking(
        public_id=f"{db_series.public_id}:{int(booking_in.start.timestamp())}",
        series_id=db_series.id,
        tutor_id=booking_in.tutor_id,
        event_type_id=db_series.event_type_id,
        timezone=booking_in.timezone,
        status="confirmed",
        student_id=db_series.student_id,
        student_first=db_series.student_first,
        student_last=db_series.student_last,
        student_email=db_series.student_email,
        student_phone=db_series.student_phone,
        parent_email=db_series.parent_email,
        parent_phone=db_series.parent_phone,
        google_event_id=new_google_event["id"],
        start=booking_in.start,
        end=booking_in.end,
    ))

    try:
        db.commit()
    except Exception as e:
        try:
            service.events().delete(calendarId=db_tutor.calendar_id, eventId=new_google_event["id"]).execute()
        except Exception:
            logging.warning(f"WARNING: DB commit failed and new calendar event {new_google_event['id']} could not be deleted. Manual cleanup required.")
        try:
            service.events().patch(calendarId=old_calendar_id, eventId=old_google_event_id, body={"recurrence": [old_rrule]}).execute()
        except Exception:
            logging.warning(f"WARNING: DB commit failed and old RRULE restoration also failed for series {db_series.id}. Manual cleanup required for event {old_google_event_id}.")
        for exc_event_id, exc_start, exc_end in future_exceptions:
            try:
                service.events().patch(calendarId=old_calendar_id, eventId=exc_event_id, body={
                    "status": "confirmed",
                    "start": {"dateTime": exc_start.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": exc_end.isoformat(), "timeZone": "UTC"},
                }).execute()
            except Exception:
                logging.warning(f"WARNING: DB commit failed and exception event {exc_event_id} could not be restored for series {db_series.id}.")
        raise HTTPException(status_code=500, detail="Failed to update booking series") from e
    db.refresh(db_series)
    return db_series


"""All series deletes are "hard" deletes that permanently remove all future bookings. Series is soft-deleted."""
@router.delete("/booking-series/{id}", response_model=BookingSeriesResponse)
def delete_booking_series(id: str, db: Session = Depends(get_db)):
    # Admin path: only the series' own state is enforced (must still be active) — same
    # reasoning as update_booking_series above. This check was previously missing entirely.
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    if not db_series.is_active:
        raise HTTPException(status_code=400, detail="Booking series is not active")
    service = get_calendar_service(SCOPES)
    return _cancel_series(db_series, db, service)


def _cancel_booking(db_booking: Booking, db: Session, service) -> Booking:
    """Cancel saga — calendar op + DB soft-delete + compensation on failure.
    Caller is responsible for all policy checks (status, notice window, is-cancellation-allowed)
    before calling this. Helper assumes the booking is valid to cancel."""
    # Save all relationship-accessed values upfront before any op that could expire the session
    calendar_id = db_booking.tutor.calendar_id
    event_id = db_booking.google_event_id
    booking_start = db_booking.start
    booking_end = db_booking.end
    booking_id = db_booking.id
    series_google_event_id = db_booking.series.google_event_id if db_booking.series_id is not None else None

    # Track the exact event ID deleted so we can restore it on commit failure
    deleted_cal_event_id = None

    if db_booking.series_id is not None:
        # Series: cancel specific instance only, keeping the master RRULE event intact.
        try:
            if event_id != series_google_event_id:
                # Exception event — has its own ID, delete directly
                deleted_cal_event_id = event_id
                service.events().delete(calendarId=calendar_id, eventId=deleted_cal_event_id).execute()
            else:
                # Normal occurrence — look up specific instance under the master
                instances = service.events().instances(
                    calendarId=calendar_id,
                    eventId=series_google_event_id,
                    timeMin=(booking_start - timedelta(seconds=1)).isoformat(),
                    timeMax=(booking_end + timedelta(seconds=1)).isoformat(),
                ).execute()
                if not instances.get("items"):
                    raise HTTPException(status_code=400, detail="Calendar instance not found for this booking, cannot cancel")
                deleted_cal_event_id = instances["items"][0]["id"]
                service.events().delete(calendarId=calendar_id, eventId=deleted_cal_event_id).execute()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to delete calendar event") from e
    else:
        deleted_cal_event_id = event_id
        try:
            service.events().delete(calendarId=calendar_id, eventId=deleted_cal_event_id).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to delete calendar event") from e

    db_booking.status = "cancelled"
    try:
        db.commit()
    except Exception as e:
        # Compensate: restore the event on Google Calendar.
        # Google soft-deletes events (status="cancelled") — patch back to confirmed to restore.
        # Works for both series instances (tombstone exceptions) and standalone events.
        try:
            service.events().patch(calendarId=calendar_id, eventId=deleted_cal_event_id, body={
                "status": "confirmed",
                "start": {"dateTime": booking_start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": booking_end.isoformat(), "timeZone": "UTC"},
            }).execute()
        except Exception:
            logging.warning(f"WARNING: DB commit failed for cancel of booking {booking_id} and calendar restoration also failed for event {deleted_cal_event_id}. Manual reconciliation required.")
        raise HTTPException(status_code=500, detail="Failed to update booking status after calendar cancellation") from e
    return db_booking


@router.delete("/{ref}", response_model=BookingResponse)
def delete_booking(ref: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_booking = resolve_ref(ref, db, settings)
    # Admin path: only the booking's own state is enforced (must still be confirmed) — event-type
    # policy (cancel_mode) and the past-time notice-window floor are booker-facing rules and
    # deliberately don't apply to admin, who needs to override both (e.g. waiving a no-show fee).
    if db_booking.status != "confirmed":
        raise HTTPException(status_code=400, detail="Only confirmed bookings can be cancelled")
    service = get_calendar_service(SCOPES)
    return _cancel_booking(db_booking, db, service)


@router.get("/manage-occurrence/{ref}", response_model=BookingResponse)
def get_booking_by_ref(ref: str, db: Session = Depends(get_db)):
    # Plain lookup, same as GET /{ref} — reads never materialize a virtual occurrence.
    db_booking = db.query(Booking).filter(Booking.public_id == ref).first()
    if not db_booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return db_booking


@router.post("/manage-occurrence/{ref}/cancel", response_model=BookingResponse)
def cancel_booking_by_ref(ref: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_booking = resolve_ref(ref, db, settings)
    if db_booking.status != "confirmed":
        raise HTTPException(status_code=400, detail="Only confirmed bookings can be cancelled")
    booking_start_tz = db_booking.start if db_booking.start.tzinfo else db_booking.start.replace(tzinfo=UTC)
    minutes_until = (booking_start_tz - datetime.now(UTC)).total_seconds() / 60
    action = get_cancel_action(db_booking.event_type, minutes_until)
    if action == 'blocked':
        raise HTTPException(status_code=400, detail="Cancellation is not currently available for this booking")
    if action == 'request':
        request = BookingRequest(booking_id=db_booking.id, type='cancel_occurrence')
        db.add(request)
        try:
            db.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to submit cancellation request") from e
        return db_booking
    service = get_calendar_service(SCOPES)
    return _cancel_booking(db_booking, db, service)

@router.post("/manage-occurrence/{ref}/reschedule", response_model=BookingResponse)
def reschedule_booking_by_ref(ref: str, booking_in: BookingReschedule, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_booking = resolve_ref(ref, db, settings)
    if db_booking.status != "confirmed":
        raise HTTPException(status_code=400, detail="Only confirmed bookings can be rescheduled")
    booking_start_tz = db_booking.start if db_booking.start.tzinfo else db_booking.start.replace(tzinfo=UTC)
    minutes_until = (booking_start_tz - datetime.now(UTC)).total_seconds() / 60
    action = get_reschedule_action(db_booking.event_type, minutes_until)
    if action == 'blocked':
        raise HTTPException(status_code=400, detail="Rescheduling is not currently available for this booking")
    if action == 'request':
        request = BookingRequest(
            booking_id=db_booking.id,
            type='reschedule_occurrence',
            requested_start=booking_in.start,
            requested_end=booking_in.end,
            requested_timezone=booking_in.timezone,
            requested_tutor_id=booking_in.tutor_id,
        )
        db.add(request)
        try:
            db.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to submit reschedule request") from e
        return db_booking
    service = get_calendar_service(SCOPES)
    return _reschedule_booking(db_booking, booking_in, db, service)


@router.get("/manage-series/{ref}", response_model=BookingSeriesResponse)
def get_series_by_ref(ref: str, db: Session = Depends(get_db)):
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == ref).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    return db_series


@router.post("/manage-series/{ref}/cancel", response_model=BookingSeriesResponse)
def cancel_series_by_ref(ref: str, db: Session = Depends(get_db)):
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == ref).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    if not db_series.is_active:
        raise HTTPException(status_code=400, detail="This series has already been cancelled")
    next_booking = (
        db.query(Booking)
        .filter(Booking.series_id == db_series.id, Booking.start >= datetime.now(UTC), Booking.status == "confirmed")
        .order_by(Booking.start)
        .first()
    )
    minutes_until = (
        (next_booking.start if next_booking.start.tzinfo else next_booking.start.replace(tzinfo=UTC)) - datetime.now(UTC)
    ).total_seconds() / 60 if next_booking else float('inf')
    action = get_cancel_action(db_series.event_type, minutes_until)
    if action == 'blocked':
        raise HTTPException(status_code=400, detail="Cancellation is not currently available for this series")
    if action == 'request':
        request = BookingRequest(booking_series_id=db_series.id, type='cancel_series')
        db.add(request)
        try:
            db.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to submit cancellation request") from e
        return db_series
    service = get_calendar_service(SCOPES)
    return _cancel_series(db_series, db, service)


@router.post("/manage-series/{ref}/reschedule", response_model=BookingSeriesResponse)
def reschedule_series_by_ref(ref: str, booking_in: BookingReschedule, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == ref).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    if not db_series.is_active:
        raise HTTPException(status_code=400, detail="This series has already been cancelled")
    next_booking = (
        db.query(Booking)
        .filter(Booking.series_id == db_series.id, Booking.start >= datetime.now(UTC), Booking.status == "confirmed")
        .order_by(Booking.start)
        .first()
    )
    minutes_until = (
        (next_booking.start if next_booking.start.tzinfo else next_booking.start.replace(tzinfo=UTC)) - datetime.now(UTC)
    ).total_seconds() / 60 if next_booking else float('inf')
    action = get_reschedule_action(db_series.event_type, minutes_until)
    if action == 'blocked':
        raise HTTPException(status_code=400, detail="Rescheduling is not currently available for this series")
    if action == 'request':
        request = BookingRequest(
            booking_series_id=db_series.id,
            type='reschedule_series',
            requested_start=booking_in.start,
            requested_end=booking_in.end,
            requested_timezone=booking_in.timezone,
            requested_tutor_id=booking_in.tutor_id,
        )
        db.add(request)
        try:
            db.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to submit reschedule request") from e
        return db_series
    service = get_calendar_service(SCOPES)
    return _reschedule_series(db_series, booking_in, db, service, settings)


@router.post("/booking-request/{request_id}/approve")
def approve_pending_request(request_id: int, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_request = db.query(BookingRequest).filter(BookingRequest.id == request_id).first()
    if not db_request:
        raise HTTPException(status_code=404, detail="Request not found")
    if db_request.status != 'pending':
        raise HTTPException(status_code=400, detail="Request is not pending")
    # Set status before the helper commits — both the saga and the status update land in one transaction.
    # If the calendar op or DB commit fails, the status change is rolled back automatically.
    db_request.status = 'approved'
    service = get_calendar_service(SCOPES)
    if db_request.type == 'cancel_occurrence':
        return _cancel_booking(db_request.booking, db, service)
    elif db_request.type == 'reschedule_occurrence':
        # requested_start/end are UTC-aware (DateTime(timezone=True)). _convert_to_utc sees dt.tzinfo is not
        # None and calls .astimezone(utc) — no-op. requested_timezone preserved for display/email only.
        booking_in = BookingReschedule(
            tutor_id=db_request.requested_tutor_id,
            start=db_request.requested_start,
            end=db_request.requested_end,
            timezone=db_request.requested_timezone,
        )
        return _reschedule_booking(db_request.booking, booking_in, db, service)
    elif db_request.type == 'cancel_series':
        return _cancel_series(db_request.series, db, service)
    elif db_request.type == 'reschedule_series':
        booking_in = BookingReschedule(
            tutor_id=db_request.requested_tutor_id,
            start=db_request.requested_start,
            end=db_request.requested_end,
            timezone=db_request.requested_timezone,
        )
        return _reschedule_series(db_request.series, booking_in, db, service, settings)
    else:
        raise HTTPException(status_code=500, detail="Unexpected request type")


@router.post("/booking-request/{request_id}/deny", response_model=BookingRequestResponse)
def deny_pending_request(request_id: int, db: Session = Depends(get_db)):
    db_request = db.query(BookingRequest).filter(BookingRequest.id == request_id).first()
    if not db_request:
        raise HTTPException(status_code=404, detail="Request not found")
    if db_request.status != 'pending':
        raise HTTPException(status_code=400, detail="Request is not pending")
    db_request.status = 'denied'
    try:
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to deny request") from e
    return db_request


