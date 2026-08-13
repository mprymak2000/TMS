from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func, tuple_
from sqlalchemy.orm import Session
from models import Booking, BookingSeries, EventType, Tutor
from schemas import BookingFacets, BookingResponse, EventTypeFacetOption, StudentFacetOption, TutorFacetOption
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
        count: int | None,
        settings
    ) -> list[BookingResponse]:
    """Generates up to `count` virtual occurrences for a series within [time_min, time_max],
     skipping any date that already has a materialized Booking row (in db). Stops early if recur_until or
     time_max is reached first. Never touches the DB.

     time_min/time_max mirror Google Calendar's timeMin/timeMax — both optional. Omitting time_min
     means "since the series actually started" (series.start_date); omitting time_max means
     unbounded — count (page * page_size from the caller) is what guarantees termination in that
     case, same role pageToken/maxResults plays for Google. count=None means no cap at all — only
     safe when time_max is set (a real range guarantees termination on its own); the caller
     (merge_occurrences) only does this when both time_min and time_max are present.

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
    while count is None or len(occurrences) < count:
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
    include_cancelled: bool = False,
    order: str = "asc",
) -> tuple[list[BookingResponse], int | None, bool]:
    """Merge materialized (in db) + virtual occurrences across the given series within [time_min, time_max],
     paginate to one page. Shared by GET /bookings/ and
     GET /bookings/booking-series/{id}/occurrences. Returns (page_items, total, has_more) — total
     is only meaningful (non-None) for a bounded range; has_more is only meaningful when unbounded.

     time_min/time_max optional — omitting either
     means unbounded on that side. page/page_size remains a 'local' termination mechanism for an
     indefinite series when time_max is omitted and can be recalled indefinitely;
     time_max, when given, is an 'absolute' stop.

     include_cancelled is an independent filter, not tied to time direction — mirrors Google
     Calendar's showDeleted (default False, excludes cancelled/rescheduled-away rows; True shows
     everything). Virtual occurrences are always "confirmed" by construction, so this only
     affects the materialized-row query.

     order='desc' fetches the most-recent-first page instead of the oldest-first page — without
     this, an unbounded-below query (e.g. time_max=now, no time_min) would always paginate
     starting from the very earliest matching row, not the most recent one; reversing the already
     -fetched page client-side can't fix that, since it doesn't change which rows got fetched.
     _virtual_occurrences stays forward-only regardless (unaffected by order) — descending queries
     are only ever used for genuinely-past ranges, which are always already materialized by the
     time they're in the past, so there's nothing virtual to walk backward through.

     Whenever time_max is given, the range is bounded — cheap to walk to completion (time_max
     alone guarantees _virtual_occurrences terminates, no count cap needed: the walk's floor is
     always a real anchor either way, series.start_date if time_min is omitted, so omitting
     time_min doesn't threaten termination or cost, only widens the floor), so the true total is
     computed before slicing. page/page_size still apply normally — this isn't "return
     everything," it's "we can tell you how many pages there are." Only a missing time_max is
     genuinely unbounded (an indefinite series has no natural "last occurrence ever"), which is
     the one case that still needs the page-cap/Load-More fallback instead of a real total.
     """
    bounded = time_max is not None

    materialized_bookings_query = materialized_query
    if not include_cancelled:
        materialized_bookings_query = materialized_bookings_query.filter(Booking.status == "confirmed")
    if time_min is not None:
        materialized_bookings_query = materialized_bookings_query.filter(Booking.start >= time_min)
    if time_max is not None:
        materialized_bookings_query = materialized_bookings_query.filter(Booking.start <= time_max)
    materialized_bookings = materialized_bookings_query.all()

    needed_total = None if bounded else page * page_size + 1 # extra +1 booking to check if there is more bookings to load after the requested count is satisfied
    virtual = []
    for series in relevant_series:
        virtual.extend(_virtual_occurrences(series, time_min, time_max, needed_total, settings))

    def _sort_key(b):
        return b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC)

    merged = sorted([*materialized_bookings, *virtual], key=_sort_key, reverse=(order == "desc"))
    has_more = False if bounded else len(merged) > page * page_size
    total = len(merged) if bounded else None
    start = (page - 1) * page_size
    items = [BookingResponse.model_validate(b) for b in merged[start:start + page_size]]
    return items, total, has_more


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
    """Given a set of scope parameters, return the corresponding facet options for the respective fi."""    
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
    """ Given scope parameters, attach them to the base query and fire it as many times as there are facets, while keeping one facet type unfiltered at a time. Do this for regualar Bookings and BookingSeries. Return the unique set of facet options for each facet type. """

    # Deplicate query and apply scope filters, while excluding one filter at a time 
    tutor_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, event_type_ids, student_pairs, exclude="tutor")
    tutor_id_set = {row[0] for row in tutor_query.with_entities(Booking.tutor_id).distinct().all()} # [(1,), (2,), ...] -> {1, 2, ...}
    event_type_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, event_type_ids, student_pairs, exclude="event_type")
    event_type_id_set = {row[0] for row in event_type_query.with_entities(Booking.event_type_id).distinct().all()} # [(1,), (2,), ...] -> {1, 2, ...}
    student_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, event_type_ids, student_pairs, exclude="student")
    student_pair_set = set(student_query.with_entities(Booking.student_first, Booking.student_last).distinct().all()) # [('John', 'Doe'), ('Jane', 'Smith'), ...] -> {('John', 'Doe'), ('Jane', 'Smith'), ...}

    if series_base_query is not None:
        # the 3 outter queries are series which aren't dated. By calling _virtual_occurrences we can check for actual, scheduled ocurrences within the requested time range (if it's in the future, past is handled by standard Booking model above). If any exist, we add the corresponding facet to the set. This is done for each facet type.
        series_no_tutor = apply_scope_filters(series_base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="tutor").all()
        for series in series_no_tutor:
            # if actual 
            if _virtual_occurrences(series, time_min, time_max, 1, settings):
                tutor_id_set.add(series.tutor_id)
        series_no_event_type = apply_scope_filters(series_base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="event_type").all()
        for series in series_no_event_type:
            if _virtual_occurrences(series, time_min, time_max, 1, settings):
                event_type_id_set.add(series.event_type_id)
        series_no_student = apply_scope_filters(series_base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="student").all()
        for series in series_no_student:
            if _virtual_occurrences(series, time_min, time_max, 1, settings):
                student_pair_set.add((series.student_first, series.student_last))

    return _build_facets(tutor_id_set, event_type_id_set, student_pair_set, db)


def compute_series_facets(base_query, tutor_ids, event_type_ids, student_pairs, db):
    """ Given scope parameters, attach them to the base query for SERIES (not individual ocurrences) and fire it as many times as there are facets, while keeping one facet type unfiltered at a time. Return the unique set of facet options for each facet type. """

    tutor_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="tutor")
    tutor_id_set = {row[0] for row in tutor_query.with_entities(BookingSeries.tutor_id).distinct().all()}

    event_type_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="event_type")
    event_type_id_set = {row[0] for row in event_type_query.with_entities(BookingSeries.event_type_id).distinct().all()}

    student_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, event_type_ids, student_pairs, exclude="student")
    student_pair_set = set(student_query.with_entities(BookingSeries.student_first, BookingSeries.student_last).distinct().all())

    return _build_facets(tutor_id_set, event_type_id_set, student_pair_set, db)