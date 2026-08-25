import base64
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo
from sqlalchemy import and_, func, or_, tuple_
from sqlalchemy.orm import Session
from models import Booking, BookingSeries, EventType, Tutor
from schemas import BookingFacets, BookingResponse, EventTypeFacetOption, StudentFacetOption, TutorFacetOption
from policy import get_cancel_action, get_reschedule_action
from fastapi import HTTPException
     


def _occurrence_end(series: BookingSeries, start_local_date: date, tz: ZoneInfo) -> datetime:
    """Given a series and a candidate local start date, compute the occurrence's UTC end time."""
    start_local = datetime.combine(start_local_date, series.dtstart.time(), tzinfo=tz)
    return (start_local + series.duration).astimezone(UTC)


def _to_local_date(dt: datetime, tz: ZoneInfo) -> date:
    """Normalize a possibly-naive UTC datetime to tz-aware, return its local date. Helpful for SQLite, which doesn't support tz-aware datetimes in queries."""
    dt_utc = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return dt_utc.astimezone(tz).date()


def _ensure_occurrence(series: BookingSeries, start_utc: datetime, db: Session, settings) -> Booking:
    """Ensure a specific occurrence of a series exists. Returns existing or newly created Booking."""
    booking = db.query(Booking).filter(Booking.series_id == series.id, Booking.start == start_utc).first()
    if booking:
        # if exists, return. no-op. idempotent
        return booking
    tz = ZoneInfo(settings.business_timezone)
    start_local = start_utc.astimezone(tz)
    if start_local.weekday() != series.dtstart.weekday() or start_local.time() != series.dtstart.time():
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


def active_series_filter(today: date):
    """SQL filter expression - a series is active unless explicitly cancelled/rescheduled, or its
    until has passed. Full (customer-facing) definition - admin routes use a narrower status-only
    check instead, see routers/bookings.py."""
    return and_(
        # NULL NOT IN (...) evaluates to NULL in SQL, not True - a fresh series (status IS NULL)
        # would otherwise get silently filtered out. Handle NULL explicitly instead of relying on notin_.
        or_(BookingSeries.status == None, BookingSeries.status.notin_(['cancelled', 'rescheduled'])),
        or_(BookingSeries.until == None, BookingSeries.until >= today)
    )


def is_series_active(series: BookingSeries, today: date) -> bool:
    """Python-side equivalent of active_series_filter, for a single already-loaded series."""
    if series.status in ('cancelled', 'rescheduled'):
        return False
    return series.until is None or series.until >= today


def series_inactive_reason(series: BookingSeries, today: date) -> str:
    """Human-readable reason a series is no longer active. Verifies its claim rather than assuming -
    only meaningful once is_series_active(series, today) is already known False."""
    if series.status == 'cancelled':
        return "This series has been cancelled"
    if series.status == 'rescheduled':
        return "This series has been rescheduled"
    if series.until is not None and series.until < today:
        return "This series has ended"
    return "This series is not currently active"  # defensive fallback - shouldn't be reachable


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
    tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(tz).date()
    if not is_series_active(series, today):
        raise HTTPException(status_code=400, detail=series_inactive_reason(series, today))
    if series.until is not None:
        if _to_local_date(start_utc, tz) > series.until:
            raise HTTPException(status_code=400, detail="Occurrence is past the end of this series")

    try:
        return _ensure_occurrence(series, start_utc, db, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


## -------------- Resolving recurrent occurrences from series rules -------------- ##

def _virtual_occurrences(
        series: BookingSeries,
        time_min: datetime | None,
        time_max: datetime | None,
        count: int | None,
        settings,
        cursor: tuple[datetime, str] | None = None, # decoded pagination cursor
    ) -> list[BookingResponse]:
    """Generate up to `count` virtual occurrences for a series within [time_min, time_max],
    skipping materialized dates and anything at/before `cursor`. Never touches the DB.

    time_min/time_max are optional, mirroring Google's timeMin/timeMax. count=None is only
    safe when time_max or until bounds the walk - otherwise it never terminates.
    Cursor is a decoded (start, public_id) resume point from the client's prior page - resumes
    from there instead of walking from series.dtstart/time_min. Guaranteed to fall within
    [time_min/flor_date, time_max]: decode_cursor rejects a cursor minted under a different time window
    before this function ever sees it.
    """
    if count is None and time_max is None and series.until is None:
        raise ValueError("_virtual_occurrences: unbounded walk - series is indefinite and neither count nor time_max is set")

    tz = ZoneInfo(settings.business_timezone)
    existing_starts = {b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC) for b in series.bookings} # existing materialized occurrences part of series, to skip when generating virtual occurrences

    # Need a FLOOR so no virtual occurrences are generated before the series actually started.
    floor_date = series.dtstart.date()
    if time_min is not None:
        floor_date = max(floor_date, _to_local_date(time_min, tz))
    if cursor is not None:
        cursor_start, _ = cursor
        floor_date = max(floor_date, _to_local_date(cursor_start, tz))

    time_max_date = _to_local_date(time_max, tz) if time_max is not None else None

    # point at the first occurrence in local time, then jump forward by one week (DST safe) and generate occurrence objects
    current_date = floor_date
    days_until_next_occurrence = (series.dtstart.weekday() - current_date.weekday()) % 7 # 0 if current_date is already on the right day of week
    current_date += timedelta(days=days_until_next_occurrence) # no-op if days_until_next_occurrence is 0

    occurrences = []
    while count is None or len(occurrences) < count:
        # if series is finite, stop generating occurrences after until - can't have more occurrences
        if series.until is not None and current_date > series.until:
            break
        # if upper bound date is set, stop generating occurrences after time_max
        if time_max_date is not None and current_date > time_max_date:
            break
        start_utc = datetime.combine(current_date, series.dtstart.time(), tzinfo=tz).astimezone(UTC) # next ocurrence local -> utc
        public_id = f"{series.public_id}:{int(start_utc.timestamp())}"

        # skip occurrences already materialized, or at/before the resume point (avoids duplicating the cursor's own occurrence)
        already_materialized = start_utc in existing_starts
        # public_id only breaks ties when two different series share the exact same start_utc - start_utc is still the primary key
        at_or_before_cursor = cursor is not None and (start_utc, public_id) <= cursor

        if not already_materialized and not at_or_before_cursor:
            end_utc = _occurrence_end(series, current_date, tz)
            minutes_until = (start_utc - datetime.now(UTC)).total_seconds() / 60
            cancel_action = get_cancel_action(series.event_type, minutes_until)
            reschedule_action = get_reschedule_action(series.event_type, minutes_until)
            occurrences.append(
                BookingResponse(
                    public_id=public_id,
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
        current_date += timedelta(days=7)
    return occurrences


def scoped_virtual_occurrences(
    series_list: list[BookingSeries],
    time_min: datetime | None,
    time_max: datetime | None,
    needed_total: int | None,
    settings,
    cursor: tuple[datetime, str] | None = None,
) -> list[BookingResponse]:
    """Generate virtual occurrences for every series in series_list within [time_min, time_max],
    capped at needed_total each (None = uncapped, only safe when time_max bounds the walk)."""
    virtual = []
    for series in series_list:
        virtual.extend(_virtual_occurrences(series, time_min, time_max, needed_total, settings, cursor))
    return virtual


## -------------- Resolving real list from materialized and virtual occurrences -------------- ##


def merge_occurrences(
    virtual_occurrences: list[BookingResponse],
    materialized_bookings: list[BookingResponse],
    order: str = "asc",
) -> list[BookingResponse]:
    """Merge already-generated virtual occurrences with already-validated materialized bookings
    into one sorted list. Callers convert materialized rows to BookingResponse before calling -
    keeps this function's inputs uniform, one type, no internal ORM/schema juggling.

    order='desc' sorts most-recent-first — used for unbounded-below queries (e.g. time_max=now, no
    time_min), where paginating from the earliest match instead of the most recent would be wrong,
    and reversing an already-fetched page can't fix that since it doesn't change which rows got
    fetched in the first place."""
    def _sort_key(b):
        # (start, id) - id breaks ties on identical start, matching the cursor's own seek comparison.
        start = b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC)
        return (start, b.id)
    return sorted([*materialized_bookings, *virtual_occurrences], key=_sort_key, reverse=(order == "desc"))


## -------------- Query Scoping and Facet Computation -------------- ##

def apply_booking_time_scope(query, time_min: datetime | None, time_max: datetime | None, include_cancelled: bool):
    """Time/status scope for a Booking query — mirrors Google Calendar's showDeleted (default
    False, excludes cancelled/rescheduled-away rows). Only ever applies to Booking; BookingSeries
    has no real occurrence timestamp to scope this way directly (see apply_series_time_scope)."""
    assert query.column_descriptions[0]["entity"] is Booking, "apply_booking_time_scope only applies to Booking queries"
    if not include_cancelled:
        query = query.filter(Booking.status == "confirmed")
    if time_min is not None:
        query = query.filter(Booking.start >= time_min)
    if time_max is not None:
        query = query.filter(Booking.start <= time_max)
    return query


def apply_series_time_scope(query, time_min: datetime | None, time_max: datetime | None, tz: ZoneInfo):
    """Return the query filtered to only series that could have occurrences within [time_min, time_max].
    Floor from the series' own first Booking row (not the mutable dtstart). Ceiling is
    until when set, else the series is indefinite/always still running - no ceiling check."""
    assert query.column_descriptions[0]["entity"] is BookingSeries, "apply_series_time_scope only applies to BookingSeries queries"
    if time_max is not None:
        query = query.filter(BookingSeries.bookings.any(Booking.start <= time_max))
    if time_min is not None:
        time_min_local = _to_local_date(time_min, tz)
        query = query.filter(or_(BookingSeries.until == None, BookingSeries.until >= time_min_local))
    return query


def apply_scope_filters(query, model, tutor_ids, event_type_ids, student_pairs, email=None, exclude=None):
    """Take in a query and attach filters to it based on the provided scope parameters. Return the modified query."""
    if email:
        query = query.filter((model.student_email == email) | (model.parent_email == email))
    if tutor_ids and exclude != "tutor":
        query = query.filter(model.tutor_id.in_(tutor_ids))
    if event_type_ids and exclude != "event_type":
        query = query.filter(model.event_type_id.in_(event_type_ids))
    if student_pairs and exclude != "student":
        query = query.filter(tuple_(model.student_first, model.student_last).in_(student_pairs))
    return query


def _build_facets(tutor_ids, event_type_ids, student_pairs, db):
    """Given a set of scope parameters, return the corresponding filter/facet options for the respective fields."""    
    tutors = db.query(Tutor).filter(Tutor.id.in_(tutor_ids)).all() if tutor_ids else []
    event_types = db.query(EventType).filter(EventType.id.in_(event_type_ids)).all() if event_type_ids else []

    tutor_options = [TutorFacetOption(id=t.id, first_name=t.first_name, last_name=t.last_name) for t in tutors]
    tutor_options.sort(key=lambda t: (t.first_name.lower(), t.last_name.lower()))

    event_type_options = [EventTypeFacetOption(id=e.id, name=e.name) for e in event_types]
    event_type_options.sort(key=lambda e: e.name.lower())

    student_options = [StudentFacetOption(first_name=first, last_name=last) for first, last in student_pairs]
    student_options.sort(key=lambda s: (s.first_name.lower(), s.last_name.lower()))

    return BookingFacets(tutors=tutor_options, event_types=event_type_options, students=student_options)


def compute_timeline_facets(materialized_base_query, series_base_query, tutor_ids, event_type_ids, student_pairs, time_min, time_max, settings, db):
    """ Duplicate the base query for each facet type. Apply the scope filters to each while excluding one facet at a time. Do this for regualar Bookings and BookingSeries and marge on each facet type. materialized_base_query must already be time/status-scoped, and series_base_query already time-scoped (apply_series_time_scope, a cheap pre-filter), by the caller. Return the unique set of facet options for each facet type. """

    # get tutor options filtered by the other filters (self-exclude tutor), then query to get their ids
    tutor_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, event_type_ids, student_pairs, exclude="tutor")
    tutor_id_set = {row[0] for row in tutor_query.with_entities(Booking.tutor_id).distinct().all()} # [(1,), (2,), ...] -> {1, 2, ...}
    # get event_type options filtered by the other filters (self-exclude event_type), then query to get their ids
    event_type_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, event_type_ids, student_pairs, exclude="event_type")
    event_type_id_set = {row[0] for row in event_type_query.with_entities(Booking.event_type_id).distinct().all()} # [(1,), (2,), ...] -> {1, 2, ...}
    # get student options filtered by the other filters (self-exclude student), then query to get their first/last names (from denormalized column names on bookimg, to be changed to student identity)
    student_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, event_type_ids, student_pairs, exclude="student")
    student_pair_set = set(student_query.with_entities(Booking.student_first, Booking.student_last).distinct().all()) # [('John', 'Doe'), ('Jane', 'Smith'), ...] -> {('John', 'Doe'), ('Jane', 'Smith'), ...}

    # apply_series_time_scope already dropped series that can't possibly overlap; here we confirm
    # each survivor actually has an occurrence in [time_min, time_max] (count=1 - existence only,
    # not the full list, since every occurrence of a series shares the same tutor/event_type/student)
    if series_base_query is not None:
        for series in series_base_query.all():
            if not _virtual_occurrences(series, time_min, time_max, 1, settings):
                continue
            if (not event_type_ids or series.event_type_id in event_type_ids) and (not student_pairs or (series.student_first, series.student_last) in student_pairs):
                tutor_id_set.add(series.tutor_id)
            if (not tutor_ids or series.tutor_id in tutor_ids) and (not student_pairs or (series.student_first, series.student_last) in student_pairs):
                event_type_id_set.add(series.event_type_id)
            if (not tutor_ids or series.tutor_id in tutor_ids) and (not event_type_ids or series.event_type_id in event_type_ids):
                student_pair_set.add((series.student_first, series.student_last))

    # keep a selected value visible in its own facet even if other filters/the time window narrowed it out
    # ie tutor A selected but shifting dates yields no results. Tutor a selection still needs to be visible 
    # so user can relax the filters and get new hits for new time range
    tutor_id_set |= set(tutor_ids)
    event_type_id_set |= set(event_type_ids)
    student_pair_set |= set(student_pairs)

    return _build_facets(tutor_id_set, event_type_id_set, student_pair_set, db)


def compute_series_facets(base_query, tutor_ids, event_type_ids, student_pairs, db):
    """ Given scope parameters, attach them to the base query for SERIES (not individual ocurrences) and ficlean upre it as many times as there are facets, while keeping one facet type unfiltered at a time. Return the unique set of facet options for each facet type. """

    tutor_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="tutor")
    tutor_id_set = {row[0] for row in tutor_query.with_entities(BookingSeries.tutor_id).distinct().all()}

    event_type_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="event_type")
    event_type_id_set = {row[0] for row in event_type_query.with_entities(BookingSeries.event_type_id).distinct().all()}

    student_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="student")
    student_pair_set = set(student_query.with_entities(BookingSeries.student_first, BookingSeries.student_last).distinct().all())

    # keep a selected value visible in its own facet even if other filters narrowed it out
    tutor_id_set |= set(tutor_ids)
    event_type_id_set |= set(event_type_ids)
    student_pair_set |= set(student_pairs)

    return _build_facets(tutor_id_set, event_type_id_set, student_pair_set, db)


## -------------- Cursor for Pagination -------------- ##


def _filters_fingerprint(
    tutor_ids,
    event_type_ids,
    student_pairs,
    time_min,
    time_max,
    email,
    pending_only,
    include_cancelled,
    series_id: str | None = None,
) -> str:
    """Compute a fingerprint of everything the current query is scoped to, to be included in the cursor for pagination. This allows the
    backend to detect when a cursor is being used against a different query than it was generated for, and reject
    it instead of returning potentially nonsensical results and to prevent clients from building invalid cursors.
    Deliberately excludes page_size (a display choice, not a scope change) and order/direction (Next/Prev from the
    same resume point isn't a different query). series_id scopes a cursor to one series' own occurrences endpoint,
    None for the multi-series /bookings/ endpoint."""
    time_min_utc = (time_min if time_min.tzinfo else time_min.replace(tzinfo=UTC)) if time_min else None
    time_max_utc = (time_max if time_max.tzinfo else time_max.replace(tzinfo=UTC)) if time_max else None
    canonical = json.dumps({
        "tutor_ids": sorted(tutor_ids),
        "event_type_ids": sorted(event_type_ids),
        "student_pairs": sorted(student_pairs),
        "time_min": int(time_min_utc.timestamp()) if time_min_utc else None,
        "time_max": int(time_max_utc.timestamp()) if time_max_utc else None,
        "email": email,
        "pending_only": pending_only,
        "include_cancelled": include_cancelled,
        "series_id": series_id,
    }, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def encode_cursor(
    start: datetime,
    public_id: str,
    tutor_ids: list[int],
    event_type_ids: list[int],
    student_pairs: list[tuple[int, int]],
    time_min: datetime | None,
    time_max: datetime | None,
    email: str | None,
    pending_only: bool,
    include_cancelled: bool,
    series_id: str | None = None,
) -> str:
    """Encode a cursor for pagination. Opaque token wrapping containing start timestamp, public_id tiebreaker and filter fingerprint"""
    fingerprint = _filters_fingerprint(
        tutor_ids,
        event_type_ids,
        student_pairs,
        time_min,
        time_max,
        email,
        pending_only,
        include_cancelled,
        series_id,
    )
    start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
    payload = {"start": int(start_utc.timestamp()), "public_id": public_id, "fingerprint": fingerprint}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")



def decode_cursor(
    cursor: str,
    tutor_ids,
    event_type_ids,
    student_pairs,
    time_min,
    time_max,
    email,
    pending_only,
    include_cancelled,
    series_id: str | None = None, # only used when getting occurrences of a series
) -> tuple[datetime, str]:
    """Decode a cursor for pagination. Extract start timestamp, public_id tiebreaker and filter fingerprint from opaque token"""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))) # add = padding to make multipke of 4 for b64 correctness, then decode
        start = datetime.fromtimestamp(payload["start"], tz=UTC)
        public_id = payload["public_id"]
        fingerprint = payload["fingerprint"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")
    expected_fingerprint = _filters_fingerprint(
        tutor_ids,
        event_type_ids,
        student_pairs,
        time_min,
        time_max,
        email,
        pending_only,
        include_cancelled,
        series_id,
    )
    if fingerprint != expected_fingerprint:
        raise HTTPException(status_code=400, detail="Cursor does not match current filters")
    return start, public_id



# Once a guest/contact id exists on Booking/BookingSeries, student matching should switch from
# (student_first, student_last) pairs to that id — same shape as tutor_id/event_type_id already
# use, dropping the pair/tuple_ special-casing throughout this file.