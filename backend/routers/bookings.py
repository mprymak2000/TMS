# Occurrence generation strategy:
# - Finite series (Mode A/B): all occurrences generated at create time. recur_until is set.
# - Indefinite series (Mode C): only occurrence 1 generated at create time. recur_until=None.
#   Daily Procrastinate job (tasks.extend_series) finds these and generates the next occurrence.
#   _ensure_occurrence (booking_utils.py) handles on-demand generation for both paths.
# Debug endpoint planned: POST /bookings/series/{series_id}/ensure-occurrence?date=YYYY-MM-DD

import logging
import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import tuple_

from booking_utils import active_series_filter, apply_booking_time_scope, apply_scope_filters, apply_series_time_scope, build_rrule, compute_series_facets, compute_timeline_facets, decode_cursor, encode_cursor, indefinite_series_filter, is_indefinite, is_series_active, require_link_bookable, require_link_not_archived, require_slot_in_schedule, resolve_ref, merge_occurrences, scoped_virtual_occurrences, series_inactive_reason, series_last_date, series_step
from database import get_db, get_settings
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from gcal import SCOPES, get_calendar_service
from models import Booking, BookingRequest, BookingSeries, BookingLink, BookingType, Tutor, FREQ_DAYS
from policy import (
    get_cancel_action,
    get_reschedule_action,
)
from schemas import (
    BookingCreate,
    BookingPagedListResponse,
    BookingListResponse,
    BookingRequestResponse,
    BookingReschedule,
    BookingResponse,
    BookingSeriesListResponse,
    BookingSeriesOccurrencesResponse,
    BookingSeriesResponse,
    BookingUpdate,
    BookingSeriesUpdate,
)
from sqlalchemy.orm import Session

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
PAGE_SIZE = 10
DEFAULT_PAGE_SIZE = 250  # GET /bookings/'s default page_size, overridable by callers

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _series_response(series: BookingSeries, today: date) -> BookingSeriesResponse:
    """Build a BookingSeriesResponse with is_active set explicitly - it's not a plain ORM
    attribute (needs business-local today), so from_attributes alone can't populate it."""
    response = BookingSeriesResponse.model_validate(series)
    response.is_active = is_series_active(series, today)
    return response


@router.get("/booking-series", response_model=BookingSeriesListResponse)
def get_booking_series(
    email: str | None = Query(default=None),
    tutor_ids: list[int] = Query(default=[]),
    booking_link_ids: list[int] = Query(default=[]),
    booking_type_ids: list[int] = Query(default=[]),
    student: list[str] = Query(default=[]),
    settings=Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Filtered series list with facets, no embedded occurrences. Occurrences are fetched
    separately via GET /booking-series/{id}/occurrences."""
    student_pairs = [tuple(s.split("|", 1)) for s in student] # split string "john|doe" into tuple (john, doe)

    today = datetime.now(ZoneInfo(settings.business_timezone)).date()
    base_query = db.query(BookingSeries).filter(active_series_filter(today))
    if email:
        base_query = base_query.filter((BookingSeries.student_email == email) | (BookingSeries.parent_email == email))

    scoped_series_rows = apply_scope_filters(base_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs).all()
    results = [BookingSeriesResponse.model_validate(series) for series in scoped_series_rows]

    facets = compute_series_facets(base_query, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, db)
    return BookingSeriesListResponse(items=results, facets=facets)

@router.get("/booking-series/{id}/occurrences", response_model=BookingSeriesOccurrencesResponse)
def get_booking_series_occurrences(
    id: str,
    time_min: datetime | None = Query(default=None),
    time_max: datetime | None = Query(default=None),
    include_cancelled: bool = Query(default=False),
    order: str = Query(default="asc"),
    page_size: int = Query(default=PAGE_SIZE, ge=1, le=500),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Cursor-paginated occurrences for one series. Separate endpoint, not a series_id filter
    on GET /bookings/: a series already pins tutor/booking_link/student, so facets would be
    meaningless here, and this is a sub-resource of one specific series, not a filterable
    collection - REST nested-resource shape."""
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Booking series not found")

    decoded_cursor = None
    if cursor is not None:
        # No facet filters here — this endpoint is already scoped to one series, and series_id is
        # what keeps the cursor from being replayed against a different one.
        decoded_cursor = decode_cursor(
            cursor,
            tutor_ids=[], booking_link_ids=[], booking_type_ids=[], student_pairs=[],
            time_min=time_min, time_max=time_max,
            email=None, pending_only=False, include_cancelled=include_cancelled,
            series_id=series.public_id,
        )

    materialized_query = apply_booking_time_scope(
        db.query(Booking).filter(Booking.series_id == series.id), time_min, time_max, include_cancelled
    )
    # seek direction must flip with `order` - "next page" means "after" ascending, "before" descending
    if decoded_cursor is not None:
        cursor_start, cursor_public_id = decoded_cursor
        booking_key = tuple_(Booking.start, Booking.public_id)
        cursor_key = tuple_(cursor_start, cursor_public_id)
        past_cursor = booking_key < cursor_key if order == "desc" else booking_key > cursor_key
        materialized_query = materialized_query.filter(past_cursor)

    # execute queries, generate virtual occurrences from series rules (indefinite only — a bounded
    # series has every occurrence materialized, so materialized_query already has all of them)
    indefinite = [series] if is_indefinite(series) else []
    virtual = scoped_virtual_occurrences(indefinite, time_min, time_max, page_size + 1, settings, decoded_cursor)
    materialized = [BookingResponse.model_validate(b) for b in materialized_query.all()]

    # merge into a sorted list, take first page_size's worth, encode a cursor as a bookmark and discard the tail
    merged = merge_occurrences(virtual, materialized, order)
    items = merged[:page_size]
    next_cursor = encode_cursor(
        items[-1].start, items[-1].id,
        tutor_ids=[], booking_link_ids=[], booking_type_ids=[], student_pairs=[],
        time_min=time_min, time_max=time_max,
        email=None, pending_only=False, include_cancelled=include_cancelled,
        series_id=series.public_id,
    ) if len(merged) > page_size else None
    return BookingSeriesOccurrencesResponse(items=items, next_cursor=next_cursor)

@router.get("/", response_model=BookingListResponse)
def get_bookings(
    email: str | None = Query(default=None), # main scope
    tutor_ids: list[int] = Query(default=[]), # facet scope
    booking_link_ids: list[int] = Query(default=[]), # facet scope
    booking_type_ids: list[int] = Query(default=[]), # facet scope
    student: list[str] = Query(default=[]), # facet scope
    time_min: datetime | None = Query(default=None), # main scope - time
    time_max: datetime | None = Query(default=None), # main scope - time
    include_cancelled: bool = Query(default=False), # facet scope
    order: str = Query(default="asc"), 
    pending_only: bool = Query(default=False), # main scope - branch
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=500),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Cursor-paginated, flat list of all bookings (materialized and virtual occurrences),
    filtered by tutor_ids/booking_link_ids/student/email/pending_only, scoped by time_min/time_max.
    Facets returned alongside items. See GET /bookings/pages for the total/page-based equivalent,
    kept for API completeness only, not called by the frontend."""
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    student_pairs = [tuple(s.split("|", 1)) for s in student]

    decoded_cursor = None
    if cursor is not None:
        decoded_cursor = decode_cursor(cursor, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, time_min, time_max, email, pending_only, include_cancelled)

    # email scope is shared by both branches below - apply it once, up front (though series gets ignored for pending-only branch)
    today = datetime.now(ZoneInfo(settings.business_timezone)).date()
    materialized_query = db.query(Booking)
    series_query = db.query(BookingSeries).filter(active_series_filter(today))
    if email:
        materialized_query = materialized_query.filter((Booking.student_email == email) | (Booking.parent_email == email))
        series_query = series_query.filter((BookingSeries.student_email == email) | (BookingSeries.parent_email == email))

    ## branch on pending-only: if true, only materialized bookings with a pending request are returned. else main branch runs
    if pending_only:
        base_query = materialized_query.filter(Booking.request.has(BookingRequest.status == 'pending'))
        scoped_query = apply_scope_filters(base_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs)
        # seek direction and sort order must flip together based on `order` - "next page" means
        # "after" when ascending but "before" when descending
        booking_key = tuple_(Booking.start, Booking.public_id)
        if decoded_cursor is not None:
            cursor_start, cursor_public_id = decoded_cursor
            cursor_key = tuple_(cursor_start, cursor_public_id)
            past_cursor = booking_key < cursor_key if order == "desc" else booking_key > cursor_key
            scoped_query = scoped_query.filter(past_cursor)
        sort_cols = (Booking.start.desc(), Booking.public_id.desc()) if order == "desc" else (Booking.start.asc(), Booking.public_id.asc())
        booking_rows = scoped_query.order_by(*sort_cols).limit(page_size + 1).all()
        facets = compute_timeline_facets(base_query, None, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, None, None, settings, db) # no series scoping for pending-only request
        items = [BookingResponse.model_validate(booking) for booking in booking_rows[:page_size]]
        next_cursor = encode_cursor(items[-1].start, items[-1].id, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, time_min, time_max, email, pending_only, include_cancelled) if len(booking_rows) > page_size else None
        return BookingListResponse(items=items, next_cursor=next_cursor, facets=facets)

    ## main branch
    # scope materialized bookings by time, series by time overlap, then calculate facets
    materialized_query = apply_booking_time_scope(materialized_query, time_min, time_max, include_cancelled)
    series_query = apply_series_time_scope(series_query, time_min, time_max, ZoneInfo(settings.business_timezone))
    facets = compute_timeline_facets(materialized_query, series_query, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, time_min, time_max, settings, db)
    
    # scope Bookings and BookingSeries by tutor_ids, booking_link_ids, booking_type_ids, student_pairs, using cursor as a start point
    scoped_materialized_query = apply_scope_filters(materialized_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs)
    scoped_series_query = apply_scope_filters(series_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs)
    # seek direction must flip with `order` - "next page" means "after" ascending, "before" descending
    if decoded_cursor is not None:
        cursor_start, cursor_public_id = decoded_cursor
        booking_key = tuple_(Booking.start, Booking.public_id)
        cursor_key = tuple_(cursor_start, cursor_public_id)
        past_cursor = booking_key < cursor_key if order == "desc" else booking_key > cursor_key
        scoped_materialized_query = scoped_materialized_query.filter(past_cursor)

    # execute queries, generate virtual occurrences from series rules (indefinite only — a bounded
    # series has every occurrence materialized, so it's already in scoped_materialized_bookings)
    scoped_materialized_bookings = [BookingResponse.model_validate(b) for b in scoped_materialized_query.all()]
    scoped_series = scoped_series_query.filter(indefinite_series_filter()).all()
    scoped_virtual_bookings = scoped_virtual_occurrences(scoped_series, time_min, time_max, page_size + 1, settings, decoded_cursor)

    # merge into a sorted list, take first page_size's worth, encode a cursor as a bookmark and discard the tail
    # (the merge generates up to page_size's worth of materialized bookings and virtual occurrences generate up to 
    # page_size's worth of virtual bookings. Generate each in parallel, merge and order, cut off the tail)
    merged = merge_occurrences(scoped_virtual_bookings, scoped_materialized_bookings, order)
    items = merged[:page_size]
    next_cursor = encode_cursor(
        items[-1].start,
        items[-1].id,
        tutor_ids,
        booking_link_ids,
        booking_type_ids,
        student_pairs,
        time_min,
        time_max,
        email,
        pending_only,
        include_cancelled
    ) if len(merged) > page_size else None

    return BookingListResponse(items=items, next_cursor=next_cursor, facets=facets)


@router.get("/pages", response_model=BookingPagedListResponse)
def list_bookings(
    email: str | None = Query(default=None),
    tutor_ids: list[int] = Query(default=[]),
    booking_link_ids: list[int] = Query(default=[]),
    booking_type_ids: list[int] = Query(default=[]),
    student: list[str] = Query(default=[]),
    time_min: datetime | None = Query(default=None),
    time_max: datetime | None = Query(default=None),
    include_cancelled: bool = Query(default=False),
    order: str = Query(default="asc"),
    pending_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=500),
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Paginated, flat list of all bookings (materialized + virtual occurrences), optionally
    filtered by email/tutor_ids/booking_link_ids/pending_only, bounded by time_min/time_max."""
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    student_pairs = [tuple(s.split("|", 1)) for s in student]

    # branch on pending
    if pending_only:
        # a virtual occurrence can never have a pending BookingRequest (nothing to request
        # approval on for a row that doesn't exist yet) — real rows only, no virtual merge.
        base_query = db.query(Booking).filter(Booking.request.has(BookingRequest.status == 'pending'))
        if email:
            base_query = base_query.filter((Booking.student_email == email) | (Booking.parent_email == email))
        scoped_query = apply_scope_filters(base_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs)
        booking_rows = scoped_query.order_by(Booking.start.desc()).offset((page - 1) * page_size).limit(page_size + 1).all()
        facets = compute_timeline_facets(base_query, None, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, None, None, settings, db) # no series scoping for pending-only request
        return BookingPagedListResponse(
            items=[BookingResponse.model_validate(booking) for booking in booking_rows[:page_size]],
            total=None, # not meaningful for pending-only request
            has_more=len(booking_rows) > page_size,
            page_size=page_size,
            facets=facets,
        )

    # Get booking rows that already exist in db (materialized occurrences) and get series rows that represent weekly ocurrences.
    today = datetime.now(ZoneInfo(settings.business_timezone)).date()
    materialized_query = db.query(Booking)
    series_query = db.query(BookingSeries).filter(active_series_filter(today))

    # apply filters shared by both data types.
    if email:
        materialized_query = materialized_query.filter((Booking.student_email == email) | (Booking.parent_email == email))
        series_query = series_query.filter((BookingSeries.student_email == email) | (BookingSeries.parent_email == email))

    ## time/status-scope (materialized bookings only); series time-scoped by overlap instead
    materialized_query = apply_booking_time_scope(materialized_query, time_min, time_max, include_cancelled)
    series_query = apply_series_time_scope(series_query, time_min, time_max, ZoneInfo(settings.business_timezone))

    # get facets: self-excluding (narrows down filter types and which tutors/event types/students are available for further filtering)
    facets = compute_timeline_facets(materialized_query, series_query, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, time_min, time_max, settings, db)

    # get actual bookings (items): full scope (no exclusion) on top of the same main-scoped queries, then generate+merge virtual with materialized
    scoped_materialized_query = apply_scope_filters(materialized_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs)
    scoped_materialized_bookings = [BookingResponse.model_validate(b) for b in scoped_materialized_query.all()]

    ## time/status-scope (series only) - different mechanism than bookings materialized in db
    scoped_series_query = apply_scope_filters(series_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs) # first scope series
    # indefinite only - a bounded series has every occurrence materialized, so it's already above
    scoped_series = scoped_series_query.filter(indefinite_series_filter()).all() # execute query to get series rows.

    # generate occurrence from scoped series rows
    bounded = time_max is not None
    needed_total = None if bounded else page * page_size + 1
    scoped_virtual_bookings = scoped_virtual_occurrences(scoped_series, time_min, time_max, needed_total, settings)

    ### Combine the materialized bookings and generated occurrences from series rules
    merged = merge_occurrences(scoped_virtual_bookings, scoped_materialized_bookings, order)

    #### Pagination
    total = len(merged) if bounded else None
    has_more = False if bounded else len(merged) > page * page_size
    start = (page - 1) * page_size
    items = merged[start:start + page_size]

    return BookingPagedListResponse(items=items, total=total, has_more=has_more, page_size=page_size, facets=facets)
    


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
    if not db_tutor.is_active:
        raise HTTPException(status_code=400, detail="Tutor is not active")
    db_booking_link = db.query(BookingLink).filter(BookingLink.id == booking_in.booking_link_id).first()
    if not db_booking_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    # Authoritative — the slug lookup and slots endpoint only fail earlier and more legibly.
    require_link_bookable(db_booking_link)
    require_slot_in_schedule(db, db_booking_link, booking_in.tutor_id, booking_in.start, booking_in.end)
    BUSINESS_TZ = ZoneInfo(settings.business_timezone)
    # Conflict check — must run before touching Google Calendar so a rejection is cheap.
    # The loop walks every occurrence that would be created and queries the DB for any
    # confirmed booking that overlaps it. One hit → reject the whole request.
    _duration = booking_in.end - booking_in.start

    if db_booking_link.expires_on is not None and booking_in.start.date() > db_booking_link.expires_on:
        raise HTTPException(status_code=400, detail="Booking start is after this event type's expiry date")

    # Days between occurrences, off the link that configures the recurrence.
    _step = timedelta(days=db_booking_link.interval * FREQ_DAYS[db_booking_link.freq])

    # How the series ends — a date (UNTIL) or a quota (COUNT), never both. The admin fixes it via
    # expires_on/count; the booker picks it when the matching flag is on.
    recur_until_date = None
    recur_count = None
    if db_booking_link.recurring:
        if db_booking_link.expires_on is not None:
            recur_until_date = db_booking_link.expires_on
        elif db_booking_link.booker_can_set_recur_until and booking_in.recur_until is not None:
            recur_until_date = booking_in.recur_until
        elif db_booking_link.booker_can_set_count and booking_in.recur_count is not None:
            recur_count = booking_in.recur_count
        elif db_booking_link.count is not None:
            recur_count = db_booking_link.count

    # _gen_through: last occurrence date to conflict-check before accepting the booking. Indefinite
    # series check only occurrence 1 — the slot picker handles future conflicts.
    if db_booking_link.recurring:
        if recur_count is not None:
            _gen_through = booking_in.start.date() + (recur_count - 1) * _step
        elif recur_until_date is not None:
            _gen_through = recur_until_date
        else:
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
        occ = (occ.astimezone(BUSINESS_TZ) + _step).astimezone(UTC)

    new_public_id = str(uuid4())
    manage_path = "manage-series" if db_booking_link.recurring else "manage-occurrence"
    manage_url = f"{FRONTEND_URL}/{manage_path}/{new_public_id}"
    description_parts = [p for p in [db_booking_link.description, f"Manage your booking: {manage_url}"] if p]
    new_event = {
            "summary": f"{db_booking_link.slug}: {booking_in.student_first} and {db_tutor.first_name}",
            "description": "\n\n".join(description_parts),
            "start": {"dateTime": booking_in.start.isoformat(), "timeZone": settings.business_timezone},
            "end": {"dateTime": booking_in.end.isoformat(), "timeZone": settings.business_timezone},
        }
    if db_booking_link.recurring:
        new_event["recurrence"] = [build_rrule(db_booking_link.freq, db_booking_link.interval,
                                               recur_until_date, recur_count, tz=BUSINESS_TZ)]

    service = get_calendar_service(SCOPES)
    try:
        google_event = service.events().insert(calendarId=db_tutor.calendar_id, body=new_event).execute()
    except Exception as e:
        logging.error(f"Failed to create calendar event: {e}")
        raise HTTPException(status_code=500, detail="Failed to create calendar event") from e

    try:
        if db_booking_link.recurring:
            local_start = booking_in.start.astimezone(BUSINESS_TZ)
            local_end = booking_in.end.astimezone(BUSINESS_TZ)
            series = BookingSeries(
                public_id=new_public_id,
                **booking_in.model_dump(exclude={"start", "end", "timezone", "recur_until", "recur_count"}),
                dtstart=local_start.replace(tzinfo=None),
                dtend=local_end.replace(tzinfo=None),
                freq=db_booking_link.freq,
                interval=db_booking_link.interval,
                until=recur_until_date,
                count=recur_count,
                booking_type_id=db_booking_link.booking_type_id,
                google_event_id=google_event["id"],
            )
            db.add(series)
            db.flush()

            base_fields = booking_in.model_dump(exclude={"recur_until", "recur_count", "start", "end"})
            occ_start = booking_in.start
            first_booking = None
            while occ_start.date() <= _gen_through:
                booking = Booking(
                    public_id=f"{series.public_id}:{int(occ_start.timestamp())}",
                    **base_fields,
                    series_id=series.id,
                    booking_type_id=series.booking_type_id,
                    google_event_id=google_event["id"],
                    start=occ_start,
                    end=occ_start + _duration,
                    status="confirmed",
                )
                db.add(booking)
                if first_booking is None:
                    first_booking = booking
                occ_start = (occ_start.astimezone(BUSINESS_TZ) + _step).astimezone(UTC)
            db.commit()
            db.refresh(first_booking)
            return first_booking

        new_booking = Booking(
            public_id=new_public_id,
            **booking_in.model_dump(exclude={"recur_until", "recur_count"}),
            booking_type_id=db_booking_link.booking_type_id,
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
    # Guarded here rather than per-route, so all callers are covered (admin, manage-occurrence, request approval).
    require_link_not_archived(db_booking.booking_link)
    require_slot_in_schedule(db, db_booking.booking_link, booking_in.tutor_id, booking_in.start, booking_in.end)

    db_tutor = db.query(Tutor).filter(Tutor.id == booking_in.tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    if not db_tutor.calendar_id:
        raise HTTPException(status_code=400, detail="Tutor must have a calendar ID")
    if not db_tutor.is_active:
        raise HTTPException(status_code=400, detail="Tutor is not active")

    # Rescheduling to the exact same slot is a no-op and almost certainly a misclick — reject it.
    # Overlapping its own old time is fine and deliberately allowed (a 15-min nudge); that's what
    # get_available_slots' exclude_booking_id is for.
    _old_start = db_booking.start if db_booking.start.tzinfo else db_booking.start.replace(tzinfo=UTC)
    _old_end = db_booking.end if db_booking.end.tzinfo else db_booking.end.replace(tzinfo=UTC)
    if booking_in.start == _old_start and booking_in.end == _old_end:
        raise HTTPException(status_code=400, detail="New time is identical to the current booking time")

    is_series = db_booking.series_id is not None
    # Save all relationship-accessed values upfront before any op that could expire the session
    original_start = db_booking.start
    original_end = db_booking.end
    booking_id = db_booking.id
    old_calendar_id = db_booking.tutor.calendar_id
    old_google_event_id = db_booking.google_event_id
    old_series_google_event_id = db_booking.series.google_event_id if is_series else None
    series_public_id = db_booking.series.public_id if is_series else None
    booking_link_slug = db_booking.booking_link.slug
    booking_link_description = db_booking.booking_link.description

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
        #todo: allow custom event name templates per booking link using dynamic tags e.g. "{student_first} {student_last} - {link_slug}"
        manage_url = f"{FRONTEND_URL}/manage-occurrence/{new_public_id}"
        description_parts = [p for p in [booking_link_description, f"Manage your booking: {manage_url}"] if p]
        new_event = {
            "summary": f"{booking_link_slug}: {db_booking.student_first} and {db_tutor.first_name}",
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
        "booking_link_id": db_booking.booking_link_id,
        "booking_type_id": db_booking.booking_type_id,
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


@router.post("/booking-series/{id}/reschedule", response_model=BookingSeriesResponse)
def reschedule_booking_series(id: str, booking_in: BookingReschedule, db: Session = Depends(get_db), settings=Depends(get_settings)):
    """POST, not PUT: this creates a *new* series row with a new public_id and closes the old one, so
    a GET on this id afterwards returns the predecessor, not what you sent. The bare PUT on this
    resource is the plain-column update below."""
    # Admin path: no notice-window/policy check, same reasoning as the occurrence-level admin
    # routes above. Only checks status, not until — status guards correctness (can't act on a
    # row whose identity is already dead); until is a business rule for clients, not admins.
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    business_tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(business_tz).date()
    if db_series.status in ('cancelled', 'rescheduled'):
        raise HTTPException(status_code=400, detail=series_inactive_reason(db_series, today))
    service = get_calendar_service(SCOPES)
    new_series = _reschedule_series(db_series, booking_in, db, service, settings)
    return _series_response(new_series, today)


def _validate_link_and_type(booking_link_id: int, booking_type_id: int | None, db: Session) -> None:
    """Shared by the two plain-column PUTs. Checked here rather than left to the FK constraints so a
    bad id answers 404 instead of an unhandled IntegrityError — the constraints are still the
    guarantee, this only buys the better message.

    An archived link is rejected as a *target* because repointing is the repair path for a booking
    stranded on one: moving onto another archived link would leave its rules just as inert."""
    db_link = db.query(BookingLink).filter(BookingLink.id == booking_link_id).first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    require_link_not_archived(db_link)

    if booking_type_id is not None:
        exists = db.query(BookingType).filter(BookingType.id == booking_type_id).exists()
        if not db.query(exists).scalar():
            raise HTTPException(status_code=404, detail="Booking type not found")


@router.put("/booking-series/{id}", response_model=BookingSeriesResponse)
def update_booking_series(id: str, booking_in: BookingSeriesUpdate, db: Session = Depends(get_db), settings=Depends(get_settings)):
    """Plain-column update: contact info, kind label, governing link. Moving a series is
    POST .../reschedule; nothing here touches Google Calendar.

    Relabelling the series carries to **all** of its occurrences, past ones included. That's the
    one place a booking's type changes without being edited directly, and it's deliberate: the
    admin picked the series, so the whole series is the scope they meant. Occurrences not yet
    generated pick it up on their own, since _ensure_occurrence copies off the series row.
    """
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    _validate_link_and_type(booking_in.booking_link_id, booking_in.booking_type_id, db)

    type_changed = db_series.booking_type_id != booking_in.booking_type_id
    for key, value in booking_in.model_dump().items():
        setattr(db_series, key, value)
    if type_changed:
        db.query(Booking).filter(Booking.series_id == db_series.id).update(
            {"booking_type_id": booking_in.booking_type_id}, synchronize_session=False
        )
    db.commit()
    db.refresh(db_series)
    today = datetime.now(ZoneInfo(settings.business_timezone)).date()
    return _series_response(db_series, today)


@router.put("/{ref}", response_model=BookingResponse)
def update_booking(ref: str, booking_in: BookingUpdate, db: Session = Depends(get_db), settings=Depends(get_settings)):
    """Plain-column update: contact info, no-show flag, kind label, governing link."""
    db_booking = resolve_ref(ref, db, settings)
    _validate_link_and_type(booking_in.booking_link_id, booking_in.booking_type_id, db)

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
    """Standalone bookings only — a series occurrence can't be hard-deleted.

    The cancelled row *is* the exclusion record: it's what stops _virtual_occurrences regenerating
    that date. Delete it and the occurrence simply comes back, out of sync with the calendar instance
    that was cancelled alongside it. Google works the same way — deleting one instance leaves a
    cancelled exception object behind, and there's no harder delete. Cancel the occurrence, or
    permanently delete the whole series.

    Checked on the ref rather than the resolved row so a virtual occurrence isn't materialized just
    to be refused: a series occurrence's public_id is always the composite "{series_id}:{timestamp}".
    """
    if ":" in ref:
        raise HTTPException(
            status_code=409,
            detail="Occurrences of a series can't be permanently deleted — cancel it, or permanently delete the whole series.",
        )
    db_booking = resolve_ref(ref, db, settings)
    if db_booking.series_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Occurrences of a series can't be permanently deleted — cancel it, or permanently delete the whole series.",
        )

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


def _cancel_series(db_series: BookingSeries, today: date, tz: ZoneInfo, db: Session, service) -> BookingSeries:
    """Cancel series saga — truncates RRULE to today, deletes future occurrence rows, soft-deletes series.
    Caller is responsible for all policy checks (is_active, etc.) before calling this."""
    calendar_id = db_series.tutor.calendar_id
    event_id = db_series.google_event_id
    old_rrule = build_rrule(db_series.freq, db_series.interval, db_series.until, db_series.count, tz=tz)
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
            body={"recurrence": [build_rrule(db_series.freq, db_series.interval, until=datetime.now(UTC))]}
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to cancel future instances of the series on calendar") from e
    db.query(Booking).filter(Booking.series_id == db_series.id, Booking.start >= datetime.now(UTC)).delete(synchronize_session=False)
    db_series.status = 'cancelled'
    # Truncating turns any rule into one that ends on a date — count and until can't coexist.
    db_series.until = today
    db_series.count = None
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
    inserts a new immutable BookingSeries row for the new pattern instead of mutating the old one in place
    (old row closed instead), drops future occurrence rows from the old series, regenerates occurrence 1
    under the new one. Caller owns is_active check.
    Helper fetches and validates the new tutor and event type since they're needed for the calendar op."""
    db_tutor = db.query(Tutor).filter(Tutor.id == booking_in.tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    if not db_tutor.calendar_id:
        raise HTTPException(status_code=400, detail="Tutor must have a calendar ID")
    if not db_tutor.is_active:
        raise HTTPException(status_code=400, detail="Tutor is not active")
    db_booking_link = db.query(BookingLink).filter(BookingLink.id == db_series.booking_link_id).first()
    if not db_booking_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    require_link_not_archived(db_booking_link)
    require_slot_in_schedule(db, db_booking_link, booking_in.tutor_id, booking_in.start, booking_in.end)
    BUSINESS_TZ = ZoneInfo(settings.business_timezone)
    today = datetime.now(BUSINESS_TZ).date()

    # Same no-op guard as the occurrence-level reschedule, but a series' identity is its weekly
    # *pattern* (weekday + time-of-day + tutor), not an absolute instant — dtstart/dtend are naive
    # local. Shifting the pattern by any amount is allowed, including into its own old band;
    # get_available_slots' exclude_series_id is what makes those slots offerable in the first place.
    _new_start_local = booking_in.start.astimezone(BUSINESS_TZ).replace(tzinfo=None)
    _new_end_local = booking_in.end.astimezone(BUSINESS_TZ).replace(tzinfo=None)
    if (
        booking_in.tutor_id == db_series.tutor_id
        and _new_start_local.weekday() == db_series.dtstart.weekday()
        and _new_start_local.time() == db_series.dtstart.time()
        and _new_end_local.time() == db_series.dtend.time()
    ):
        raise HTTPException(status_code=400, detail="New schedule is identical to the current series schedule")

    old_calendar_id = db_series.tutor.calendar_id
    old_google_event_id = db_series.google_event_id
    old_rrule = build_rrule(db_series.freq, db_series.interval, db_series.until, db_series.count, tz=BUSINESS_TZ)

    # How the new series ends. Google's split: the old rule is truncated to today and the remainder
    # moves to a new rule. A date carries over untouched; a quota is reduced by what's already been
    # consumed (cancelled occurrences included — they filled their slot).
    new_until = db_series.until
    new_count = None
    if db_series.count is not None:
        # Cancelled occurrences count as consumed - they filled their slot. Rescheduled ones don't:
        # the slot moved to their replacement row, so counting the original too would spend it twice.
        consumed = db.query(Booking).filter(
            Booking.series_id == db_series.id,
            Booking.start < datetime.now(UTC),
            Booking.status != "rescheduled",
        ).count()
        new_count = max(1, db_series.count - consumed)

    # Exceptions after the pivot don't survive a this-and-following change (verified against Google's
    # own behavior). Deleted explicitly rather than relying on the truncate to do it. Saved upfront
    # so they can be restored if the DB commit fails.
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
            body={"recurrence": [build_rrule(db_series.freq, db_series.interval, until=today, tz=BUSINESS_TZ)]}
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to truncate old calendar series") from e

    # Step 2: create new RRULE event on new tutor's calendar
    rrule = build_rrule(db_series.freq, db_series.interval, new_until, new_count, tz=BUSINESS_TZ)
    series_manage_url = f"{FRONTEND_URL}/manage-series/{db_series.public_id}"
    series_description_parts = [p for p in [db_booking_link.description, f"Manage your booking: {series_manage_url}"] if p]
    new_event = {
        "summary": f"{db_booking_link.slug}: {db_series.student_first} and {db_tutor.first_name}",
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

    # Step 3: drop future occurrence rows from the OLD series (individually-rescheduled ones
    # included — nothing after the pivot survives), insert a NEW series row for the new pattern,
    # close the old one, materialize under the new series. A past row whose replacement is dropped
    # here keeps status='rescheduled' with rescheduled_to SET NULL — the move still happened.
    db.query(Booking).filter(
        Booking.series_id == db_series.id,
        Booking.start >= datetime.now(UTC)
    # "fetch" rather than False: this delete is immediately followed by inserts, and a bulk delete
    # that doesn't sync leaves the deleted rows in the session's identity map. A new row reusing a
    # freed PK then collides with a stale entry for it.
    ).delete(synchronize_session="fetch")
    local_start = booking_in.start.astimezone(BUSINESS_TZ)
    local_end = booking_in.end.astimezone(BUSINESS_TZ)

    new_series = BookingSeries(
        public_id=str(uuid4()),
        tutor_id=booking_in.tutor_id,
        booking_link_id=db_series.booking_link_id,
        booking_type_id=db_series.booking_type_id,
        dtstart=local_start.replace(tzinfo=None),
        dtend=local_end.replace(tzinfo=None),
        freq=db_series.freq,
        interval=db_series.interval,
        until=new_until,
        count=new_count,
        google_event_id=new_google_event["id"],
        student_id=db_series.student_id,
        student_first=db_series.student_first,
        student_last=db_series.student_last,
        student_email=db_series.student_email,
        student_phone=db_series.student_phone,
        parent_email=db_series.parent_email,
        parent_phone=db_series.parent_phone,
    )
    db.add(new_series)
    db.flush()

    db_series.status = 'rescheduled'
    # Truncating ends the old rule on a date whatever it was before — count and until can't coexist.
    db_series.until = today
    db_series.count = None
    db_series.rescheduled_to = new_series.id

    # Materialize the new series: a bounded one gets every occurrence up front (same as creation —
    # only indefinite series are kept sparse for the extender to fill in), so just occurrence 1 there.
    _duration = booking_in.end - booking_in.start
    _step = series_step(new_series)
    gen_through = series_last_date(new_series)
    if gen_through is None:
        gen_through = booking_in.start.date()
    occ_start = booking_in.start
    while occ_start.date() <= gen_through:
        db.add(Booking(
            public_id=f"{new_series.public_id}:{int(occ_start.timestamp())}",
            series_id=new_series.id,
            tutor_id=booking_in.tutor_id,
            booking_link_id=db_series.booking_link_id,
            booking_type_id=new_series.booking_type_id,
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
            start=occ_start,
            end=occ_start + _duration,
        ))
        occ_start = (occ_start.astimezone(BUSINESS_TZ) + _step).astimezone(UTC)

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
    db.refresh(new_series)
    return new_series


"""All series deletes "hard" delete future occurrences, while preseving past ones. Series itself is soft-deleted."""
@router.delete("/booking-series/{id}", response_model=BookingSeriesResponse)
def delete_booking_series(id: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    # Admin path: same reasoning as update_booking_series above — status guards correctness
    # (can't act on a row whose identity is already dead), until is a client-only business rule.
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    business_tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(business_tz).date()
    if db_series.status in ('cancelled', 'rescheduled'):
        raise HTTPException(status_code=400, detail=series_inactive_reason(db_series, today))
    service = get_calendar_service(SCOPES)
    result = _cancel_series(db_series, today, business_tz, db, service)
    return _series_response(result, today)


@router.delete("/booking-series/{id}/permanent", status_code=204)
def permanently_delete_booking_series(id: str, cascade: bool = False, db: Session = Depends(get_db)):
    """Hard-delete a series and all its Bookings (past and future). Same two-round-trip
    predecessor-chain confirm flow as permanently_delete_booking above"""
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == id).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")

    immediate_predecessor = db.query(BookingSeries).filter(BookingSeries.rescheduled_to == db_series.id).first()
    if immediate_predecessor and not cascade:
        raise HTTPException(status_code=409, detail="This series has a rescheduled predecessor.")

    predecessors = []
    if cascade:
        current = immediate_predecessor
        while current is not None:
            predecessors.append(current)
            current = db.query(BookingSeries).filter(BookingSeries.rescheduled_to == current.id).first()

    all_series = predecessors + [db_series]
    service = get_calendar_service(SCOPES)
    for series in all_series:
        calendar_id = series.tutor.calendar_id
        if calendar_id and series.google_event_id:
            try:
                service.events().delete(calendarId=calendar_id, eventId=series.google_event_id).execute()
            except Exception as e:
                logging.warning(f"Permanent delete: calendar event {series.google_event_id} could not be deleted: {e}")
        db.query(Booking).filter(Booking.series_id == series.id).delete(synchronize_session=False)
        db.delete(series)
    try:
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to permanently delete booking series") from e
    return Response(status_code=204)



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
    """Soft: sets status='cancelled'. The row survives for cap counting and no-show history —
    same shape as Stripe and Google Calendar, where DELETE means "remove this from my view" and
    soft-vs-hard is the server's business. `.../permanent` is the escape hatch."""
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
    action = get_cancel_action(db_booking.booking_link, minutes_until)
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
    action = get_reschedule_action(db_booking.booking_link, minutes_until)
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
def get_series_by_ref(ref: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == ref).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    today = datetime.now(ZoneInfo(settings.business_timezone)).date()
    return _series_response(db_series, today)


@router.post("/manage-series/{ref}/cancel", response_model=BookingSeriesResponse)
def cancel_series_by_ref(ref: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == ref).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    business_tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(business_tz).date()
    if not is_series_active(db_series, today):
        raise HTTPException(status_code=400, detail=series_inactive_reason(db_series, today))
    next_booking = (
        db.query(Booking)
        .filter(Booking.series_id == db_series.id, Booking.start >= datetime.now(UTC), Booking.status == "confirmed")
        .order_by(Booking.start)
        .first()
    )
    minutes_until = (
        (next_booking.start if next_booking.start.tzinfo else next_booking.start.replace(tzinfo=UTC)) - datetime.now(UTC)
    ).total_seconds() / 60 if next_booking else float('inf')
    action = get_cancel_action(db_series.booking_link, minutes_until)
    if action == 'blocked':
        raise HTTPException(status_code=400, detail="Cancellation is not currently available for this series")
    if action == 'request':
        request = BookingRequest(booking_series_id=db_series.id, type='cancel_series')
        db.add(request)
        try:
            db.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to submit cancellation request") from e
        return _series_response(db_series, today)
    service = get_calendar_service(SCOPES)
    result = _cancel_series(db_series, today, business_tz, db, service)
    return _series_response(result, today)


@router.post("/manage-series/{ref}/reschedule", response_model=BookingSeriesResponse)
def reschedule_series_by_ref(ref: str, booking_in: BookingReschedule, db: Session = Depends(get_db), settings=Depends(get_settings)):
    db_series = db.query(BookingSeries).filter(BookingSeries.public_id == ref).first()
    if not db_series:
        raise HTTPException(status_code=404, detail="Booking series not found")
    business_tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(business_tz).date()
    if not is_series_active(db_series, today):
        raise HTTPException(status_code=400, detail=series_inactive_reason(db_series, today))
    next_booking = (
        db.query(Booking)
        .filter(Booking.series_id == db_series.id, Booking.start >= datetime.now(UTC), Booking.status == "confirmed")
        .order_by(Booking.start)
        .first()
    )
    minutes_until = (
        (next_booking.start if next_booking.start.tzinfo else next_booking.start.replace(tzinfo=UTC)) - datetime.now(UTC)
    ).total_seconds() / 60 if next_booking else float('inf')
    action = get_reschedule_action(db_series.booking_link, minutes_until)
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
        return _series_response(db_series, today)
    service = get_calendar_service(SCOPES)
    new_series = _reschedule_series(db_series, booking_in, db, service, settings)
    return _series_response(new_series, today)


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
        business_tz = ZoneInfo(settings.business_timezone)
        today = datetime.now(business_tz).date()
        return _cancel_series(db_request.series, today, business_tz, db, service)
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


