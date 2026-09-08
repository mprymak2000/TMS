import base64
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo
from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.orm import Session
from models import Booking, BookingSeries, BookingLink, BookingLinkAvailability, BookingType, Tutor, FREQ_DAYS
from schemas import BookingFacets, BookingResponse, BookingLinkFacetOption, BookingTypeFacetOption, StudentFacetOption, TutorFacetOption
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


## -------------- Recurrence rule (freq/interval/until/count) -------------- ##
# until and count are mutually exclusive per RFC5545. Everything that needs to reason about where a
# series ends goes through one of the three helpers below rather than reading the columns directly.

def series_step(series: BookingSeries) -> timedelta:
    """Time between consecutive occurrences of a series' rule."""
    return timedelta(days=series.interval * FREQ_DAYS[series.freq])


def is_indefinite(series: BookingSeries) -> bool:
    """Python-side equivalent of indefinite_series_filter, for a single already-loaded series."""
    return series.until is None and series.count is None


def series_last_date(series: BookingSeries) -> date | None:
    """Last local date the rule can generate on, or None if indefinite.

    count is an arithmetic progression from dtstart, so it resolves to a date rather than needing an
    occurrence tally."""
    if series.count is not None:
        return series.dtstart.date() + (series.count - 1) * series_step(series)
    return series.until


def build_rrule(freq: str, interval: int, until: date | datetime | None = None,
                count: int | None = None, tz: ZoneInfo | None = None) -> str:
    """iCal RRULE line for a recurrence. Emits COUNT or UNTIL, never both.

    UNTIL is an absolute instant in UTC, which RFC5545 requires whenever DTSTART is a date-time
    (ours always is). So a `date` until — "runs through this day" — resolves to the END of that day
    in `tz`, then converts: Dec 9 in America/New_York is 20261210T045959Z, NOT 20261209T235959Z.
    That naive form is Dec 9 18:59 local and would drop a 7pm session that evening.

    A datetime `until` is already an instant (a mid-day truncation) and only gets converted to UTC."""
    rrule = f"RRULE:FREQ={freq}"
    if interval != 1:
        rrule += f";INTERVAL={interval}"
    if count is not None:
        rrule += f";COUNT={count}"
    elif until is not None:
        if isinstance(until, datetime):  # checked first — datetime is a subclass of date
            until_utc = until.astimezone(UTC)
        else:
            if tz is None:
                raise ValueError("build_rrule: a date `until` needs tz to resolve to an instant")
            until_utc = datetime.combine(until, time(23, 59, 59), tzinfo=tz).astimezone(UTC)
        rrule += f";UNTIL={until_utc.strftime('%Y%m%dT%H%M%SZ')}"
    return rrule


def _ensure_occurrence(series: BookingSeries, start_utc: datetime, db: Session, settings) -> Booking:
    """Ensure a specific occurrence of a series exists. Returns existing or newly created Booking.

    Every field is copied off the series, never off its link — the series governs everything it hasn't
    generated yet, so a link edited since must not reach into an existing series' future occurrences.
    """
    booking = db.query(Booking).filter(Booking.series_id == series.id, Booking.start == start_utc).first()
    if booking:
        # if exists, return. no-op. idempotent
        return booking
    tz = ZoneInfo(settings.business_timezone)
    if series.status in ('cancelled', 'rescheduled'):
        raise ValueError("Cannot materialize an occurrence for a cancelled or rescheduled series")
    start_local = start_utc.astimezone(tz)
    last_date = series_last_date(series)
    if last_date is not None and start_local.date() > last_date:
        raise ValueError("Datetime is past the end of this series")
    # Must land exactly on the rule's grid: a whole number of steps from dtstart, same time of day.
    # Matching weekday alone would let an off-week date through once interval > 1.
    days_from_start = (start_local.date() - series.dtstart.date()).days
    if days_from_start % series_step(series).days != 0 or start_local.time() != series.dtstart.time():
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
        booking_link_id=series.booking_link_id,
        booking_type_id=series.booking_type_id,
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


## -------------- BookingLink status guards -------------- ##
# Two distinct questions, not one "is it active" check. A paused link still has live calendar rules,
# so it can still serve a reschedule — it just can't take new bookings. Only archived kills both.

def require_link_bookable(link) -> None:
    """New bookings only. Blocks paused and archived, with a reason the caller can show verbatim."""
    if link.status == "paused":
        raise HTTPException(status_code=400, detail="This booking link is paused and isn't accepting new bookings right now.")
    if link.status == "archived":
        raise HTTPException(status_code=400, detail="This booking link is no longer offered.")


def require_slot_in_schedule(db: Session, link, tutor_id: int, start_utc: datetime, end_utc: datetime) -> None:
    """The requested slot must sit inside the tutor's schedule for this link.

    Every other calendar rule is applied by /available-slots, which runs BEFORE the write and can
    simply be skipped — a direct POST reaches the router having consulted nothing. This is the write
    path's own floor, so no slot can be booked on a day the tutor doesn't work.

    Only occurrence 1 is checked: a series repeats on the same weekday and time, so if the first
    lands inside the schedule every later one does too."""
    availability = db.query(BookingLinkAvailability).filter(
        BookingLinkAvailability.booking_link_id == link.id,
        BookingLinkAvailability.tutor_id == tutor_id,
    ).first()
    if availability is None:
        raise HTTPException(status_code=400, detail="This tutor does not host this booking link")

    schedule = availability.schedule
    tz = ZoneInfo(schedule.timezone)
    start_local = start_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)

    for day in schedule.days:
        # A window ending at/before it starts wraps past midnight, so a slot can belong to a window
        # anchored on the previous local day.
        for anchor in (start_local.date(), start_local.date() - timedelta(days=1)):
            if anchor.weekday() != day.day_of_week:
                continue
            window_start = datetime.combine(anchor, day.start_time, tzinfo=tz)
            window_end = datetime.combine(anchor, day.end_time, tzinfo=tz)
            if day.end_time <= day.start_time:
                window_end += timedelta(days=1)
            if window_start <= start_local and end_local <= window_end:
                return

    raise HTTPException(
        status_code=400,
        detail="That time is outside this tutor's availability for this booking link",
    )


def require_link_not_archived(link) -> None:
    """Anything needing the link's calendar rules to still resolve — reschedules, edits.

    Paused passes: its rules are live. Archived doesn't: its rules are inert, and the fix is to
    reassign the booking to a live link rather than to revive the link."""
    if link.status == "archived":
        raise HTTPException(
            status_code=400,
            detail="This booking link is archived, so its scheduling rules no longer apply. Reassign this booking to an active link first.",
        )


def indefinite_series_filter():
    """SQL filter expression - a series whose rule has no end, so occurrences must be materialized
    on a rolling basis rather than all at creation."""
    return and_(BookingSeries.until == None, BookingSeries.count == None)


def active_series_filter(today: date):
    """SQL filter expression - a series is active unless explicitly cancelled/rescheduled, or it has
    run out of occurrences. Full (customer-facing) definition - admin routes use a narrower
    status-only check instead, see routers/bookings.py.

    A finite series is running while any of its occurrences hasn't passed, asked of the bookings
    rather than of until/count. Every finite occurrence exists from creation, so this is exact - and
    unlike the rule, it follows an occurrence that was rescheduled past where the rule ends.

    Deliberately says nothing about the series' BookingLink: a series is its own booking template
    and keeps running whatever its link's status is."""
    today_start = datetime(today.year, today.month, today.day, tzinfo=UTC)
    return and_(
        # NULL NOT IN (...) evaluates to NULL in SQL, not True - a fresh series (status IS NULL)
        # would otherwise get silently filtered out. Handle NULL explicitly instead of relying on notin_.
        or_(BookingSeries.status == None, BookingSeries.status.notin_(['cancelled', 'rescheduled'])),
        or_(
            indefinite_series_filter(),
            select(Booking.id).where(and_(
                Booking.series_id == BookingSeries.id,
                Booking.start >= today_start,
            )).exists(),
        ),
    )


def is_series_active(series: BookingSeries, today: date) -> bool:
    """Python-side equivalent of active_series_filter, for a single already-loaded series."""
    if series.status in ('cancelled', 'rescheduled'):
        return False
    if series.until is None and series.count is None:
        return True
    return any(_to_local_date(b.start, ZoneInfo("UTC")) >= today for b in series.bookings)


def series_inactive_reason(series: BookingSeries, today: date) -> str:
    """Human-readable reason a series is no longer active. Verifies its claim rather than assuming -
    only meaningful once is_series_active(series, today) is already known False."""
    if series.status == 'cancelled':
        return "This series has been cancelled"
    if series.status == 'rescheduled':
        return "This series has been rescheduled"
    if series.until is not None or series.count is not None:
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
    last_date = series_last_date(series)
    if last_date is not None and _to_local_date(start_utc, tz) > last_date:
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
    """Generate up to `count` virtual occurrences for an INDEFINITE series within [time_min, time_max],
    skipping materialized dates and anything at/before `cursor`. Never touches the DB.

    Indefinite only: a bounded series has every occurrence materialized from creation, so it has
    nothing left to generate. Callers scope with indefinite_series_filter / is_indefinite.

    time_min/time_max are optional, mirroring Google's timeMin/timeMax. count=None is only
    safe when time_max bounds the walk - otherwise it never terminates.
    Cursor is a decoded (start, public_id) resume point from the client's prior page - resumes
    from there instead of walking from series.dtstart/time_min. Guaranteed to fall within
    [time_min/flor_date, time_max]: decode_cursor rejects a cursor minted under a different time window
    before this function ever sees it.
    """
    assert is_indefinite(series), "_virtual_occurrences only applies to indefinite series"
    if count is None and time_max is None:
        raise ValueError("_virtual_occurrences: unbounded walk - neither count nor time_max is set")

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

    # point at the first occurrence in local time, then jump forward one step at a time (DST safe) and generate occurrence objects
    current_date = floor_date
    days_until_next_occurrence = (series.dtstart.weekday() - current_date.weekday()) % 7 # 0 if current_date is already on the right day of week
    current_date += timedelta(days=days_until_next_occurrence) # no-op if days_until_next_occurrence is 0

    occurrences = []
    while count is None or len(occurrences) < count:
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
            cancel_action = get_cancel_action(series.booking_link, minutes_until)
            reschedule_action = get_reschedule_action(series.booking_link, minutes_until)
            occurrences.append(
                BookingResponse(
                    public_id=public_id,
                    series_public_id=series.public_id,
                    rescheduled_to_public_id=None,
                    tutor_id=series.tutor_id,
                    booking_link_id=series.booking_link_id,
                    booking_type_id=series.booking_type_id,
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
        current_date += timedelta(days=series.interval * FREQ_DAYS[series.freq])
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
    Floor from the series' own dtstart (immutable, always honest - safe to trust directly now).
    Ceiling is until when set, else the series is indefinite/always still running - no ceiling check."""
    assert query.column_descriptions[0]["entity"] is BookingSeries, "apply_series_time_scope only applies to BookingSeries queries"
    if time_max is not None:
        # Convert time_max once, in Python, to a naive local datetime - dtstart is already naive
        # local, so this keeps the comparison entirely in one frame (no per-row SQL conversion).
        time_max_utc = time_max if time_max.tzinfo else time_max.replace(tzinfo=UTC)
        time_max_local = time_max_utc.astimezone(tz).replace(tzinfo=None)
        query = query.filter(BookingSeries.dtstart <= time_max_local)
    if time_min is not None:
        time_min_local = _to_local_date(time_min, tz)
        query = query.filter(or_(BookingSeries.until == None, BookingSeries.until >= time_min_local))
    return query


def apply_scope_filters(query, model, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, email=None, exclude=None):
    """Take in a query and attach filters to it based on the provided scope parameters. Return the modified query."""
    if email:
        query = query.filter((model.student_email == email) | (model.parent_email == email))
    if tutor_ids and exclude != "tutor":
        query = query.filter(model.tutor_id.in_(tutor_ids))
    if booking_link_ids and exclude != "booking_link":
        query = query.filter(model.booking_link_id.in_(booking_link_ids))
    if booking_type_ids and exclude != "booking_type":
        query = query.filter(model.booking_type_id.in_(booking_type_ids))
    if student_pairs and exclude != "student":
        query = query.filter(tuple_(model.student_first, model.student_last).in_(student_pairs))
    return query


def _build_facets(tutor_ids, booking_link_ids, booking_type_ids, student_pairs, db):
    """Given a set of scope parameters, return the corresponding filter/facet options for the respective fields.

    Links are looked up by id without a status filter — an archived link's bookings still group under
    it, so its facet option has to keep resolving."""
    tutors = db.query(Tutor).filter(Tutor.id.in_(tutor_ids)).all() if tutor_ids else []
    booking_links = db.query(BookingLink).filter(BookingLink.id.in_(booking_link_ids)).all() if booking_link_ids else []
    booking_types = db.query(BookingType).filter(BookingType.id.in_(booking_type_ids)).all() if booking_type_ids else []

    tutor_options = [TutorFacetOption(id=t.id, first_name=t.first_name, last_name=t.last_name) for t in tutors]
    tutor_options.sort(key=lambda t: (t.first_name.lower(), t.last_name.lower()))

    booking_link_options = [BookingLinkFacetOption(id=l.id, slug=l.slug) for l in booking_links]
    booking_link_options.sort(key=lambda l: l.slug.lower())

    booking_type_options = [BookingTypeFacetOption(id=t.id, label=t.label, color=t.color) for t in booking_types]
    booking_type_options.sort(key=lambda t: t.label.lower())

    student_options = [StudentFacetOption(first_name=first, last_name=last) for first, last in student_pairs]
    student_options.sort(key=lambda s: (s.first_name.lower(), s.last_name.lower()))

    return BookingFacets(
        tutors=tutor_options,
        booking_links=booking_link_options,
        booking_types=booking_type_options,
        students=student_options,
    )


def _series_in_scope(series, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude) -> bool:
    """In-Python mirror of apply_scope_filters for a single series row, with the same self-exclusion
    semantics. Series are checked in Python rather than SQL because whether one contributes to a
    window depends on _virtual_occurrences, which the query can't express.
    
    For ONE particular series, does it pass every active filter aside from the excluded one? If it fails even one, return false
    (don't add it to the list of facet options for the facet that's being excluded)
    """
    if exclude != "tutor" and tutor_ids and series.tutor_id not in tutor_ids:
        return False
    if exclude != "booking_link" and booking_link_ids and series.booking_link_id not in booking_link_ids:
        return False
    if exclude != "booking_type" and booking_type_ids and series.booking_type_id not in booking_type_ids:
        return False
    if exclude != "student" and student_pairs and (series.student_first, series.student_last) not in student_pairs:
        return False
    return True


def compute_timeline_facets(materialized_base_query, series_base_query, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, time_min, time_max, settings, db):
    """ Duplicate the base query for each facet type. Apply the scope filters to each while excluding one facet at a time. Do this for regualar Bookings and BookingSeries and marge on each facet type. materialized_base_query must already be time/status-scoped, and series_base_query already time-scoped (apply_series_time_scope, a cheap pre-filter), by the caller. Return the unique set of facet options for each facet type. """

    # get tutor options filtered by the other filters (self-exclude tutor), then query to get their ids
    tutor_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="tutor")
    tutor_id_set = {row[0] for row in tutor_query.with_entities(Booking.tutor_id).distinct().all()} # [(1,), (2,), ...] -> {1, 2, ...}
    # get booking_link options filtered by the other filters (self-exclude booking_link), then query to get their ids
    booking_link_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="booking_link")
    booking_link_id_set = {row[0] for row in booking_link_query.with_entities(Booking.booking_link_id).distinct().all()} # [(1,), (2,), ...] -> {1, 2, ...}
    # same for the kind label; None is dropped since an untyped booking isn't a facet option
    booking_type_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="booking_type")
    booking_type_id_set = {row[0] for row in booking_type_query.with_entities(Booking.booking_type_id).distinct().all() if row[0] is not None}
    # get student options filtered by the other filters (self-exclude student), then query to get their first/last names (from denormalized column names on bookimg, to be changed to student identity)
    student_query = apply_scope_filters(materialized_base_query, Booking, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="student")
    student_pair_set = set(student_query.with_entities(Booking.student_first, Booking.student_last).distinct().all()) # [('John', 'Doe'), ('Jane', 'Smith'), ...] -> {('John', 'Doe'), ('Jane', 'Smith'), ...}

    # apply_series_time_scope already dropped series that can't possibly overlap; here we confirm
    # each survivor actually has an occurrence in [time_min, time_max] (count=1 - existence only,
    # not the full list, since every occurrence of a series shares the same tutor/booking_link/student).
    # Indefinite only - a bounded series is fully materialized, so it already contributed above.
    if series_base_query is not None:
        for series in series_base_query.filter(indefinite_series_filter()).all():
            if not _virtual_occurrences(series, time_min, time_max, 1, settings):
                continue
            if _series_in_scope(series, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="tutor"):
                tutor_id_set.add(series.tutor_id)
            if _series_in_scope(series, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="booking_link"):
                booking_link_id_set.add(series.booking_link_id)
            if series.booking_type_id is not None and _series_in_scope(series, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="booking_type"):
                booking_type_id_set.add(series.booking_type_id)
            if _series_in_scope(series, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="student"):
                student_pair_set.add((series.student_first, series.student_last))

    # keep a selected value visible in its own facet even if other filters/the time window narrowed it out
    # ie tutor A selected but shifting dates yields no results. Tutor a selection still needs to be visible
    # so user can relax the filters and get new hits for new time range
    tutor_id_set |= set(tutor_ids)
    booking_link_id_set |= set(booking_link_ids)
    booking_type_id_set |= set(booking_type_ids)
    student_pair_set |= set(student_pairs)

    return _build_facets(tutor_id_set, booking_link_id_set, booking_type_id_set, student_pair_set, db)


def compute_series_facets(base_query, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, db):
    """ Given scope parameters, attach them to the base query for SERIES (not individual ocurrences) and ficlean upre it as many times as there are facets, while keeping one facet type unfiltered at a time. Return the unique set of facet options for each facet type. """

    tutor_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="tutor")
    tutor_id_set = {row[0] for row in tutor_query.with_entities(BookingSeries.tutor_id).distinct().all()}

    booking_link_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="booking_link")
    booking_link_id_set = {row[0] for row in booking_link_query.with_entities(BookingSeries.booking_link_id).distinct().all()}

    booking_type_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="booking_type")
    booking_type_id_set = {row[0] for row in booking_type_query.with_entities(BookingSeries.booking_type_id).distinct().all() if row[0] is not None}

    student_query = apply_scope_filters(base_query, BookingSeries, tutor_ids, booking_link_ids, booking_type_ids, student_pairs, exclude="student")
    student_pair_set = set(student_query.with_entities(BookingSeries.student_first, BookingSeries.student_last).distinct().all())

    # keep a selected value visible in its own facet even if other filters narrowed it out
    tutor_id_set |= set(tutor_ids)
    booking_link_id_set |= set(booking_link_ids)
    booking_type_id_set |= set(booking_type_ids)
    student_pair_set |= set(student_pairs)

    return _build_facets(tutor_id_set, booking_link_id_set, booking_type_id_set, student_pair_set, db)


## -------------- Cursor for Pagination -------------- ##


def _filters_fingerprint(
    tutor_ids,
    booking_link_ids,
    booking_type_ids,
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
        "booking_link_ids": sorted(booking_link_ids),
        "booking_type_ids": sorted(booking_type_ids),
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
    booking_link_ids: list[int],
    booking_type_ids: list[int],
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
        booking_link_ids,
        booking_type_ids,
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
    booking_link_ids,
    booking_type_ids,
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
        booking_link_ids,
        booking_type_ids,
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
# (student_first, student_last) pairs to that id — same shape as tutor_id/booking_link_id already
# use, dropping the pair/tuple_ special-casing throughout this file.