"""
Availability algorithm — final implementation.

WHAT THIS DOES
For each tutor, resolve their date-naive weekly schedule into concrete, merged
free_blocks (handles multi-block days and midnight-crossing schedules — see
resolve_schedule). For infinite event types specifically, thinning happens BEFORE
any date is picked at all: merge the schedule rows, subtract every existing infinite
rule's occupied span via a two-pointer sweep, all in (weekday, time) space, then
resolve only the survivors to their first concrete occurrence — see
thin_schedule_dateless / resolve_to_first_occurrence. Then, per occurrence of the
viewing window:
  - generate every candidate slot across all free_blocks at once (a batch, not one
    at a time)
  - materialize active infinite rules into real dated occurrences for just the dates
    this batch touches, fold them in alongside real busy blocks, and sweep the whole
    batch against both together in one pass
  - for finite/infinite event types, repeat that sweep week over week, letting the
    candidate batch shrink as slots die, until the series ends or the horizon is hit
  - whatever survives every check is a genuinely free, bookable slot

TIME COMPLEXITY
Let C = candidates in a batch, N = weeks checked (recur_weeks, or weeks to horizon),
R = active infinite rules for the tutor, B = real busy blocks per week.

  Per-candidate walk (naive):  O(C x N x (R + B))
    Each candidate independently scans R rules and B busy blocks, every single week.

  Batched two-pointer sweep (this algorithm):  O(N x (C + R + B))
    One shared sweep per week checks the whole surviving batch against materialized
    rules and busy blocks together — turning the C x (R+B) multiplication into a
    C + R + B addition, since candidates share one pass instead of each repeating
    the scan. Materializing R per week (not to any horizon) keeps that term small;
    C also only shrinks week to week (never grows), so real-world cost is often
    better than this worst-case bound.

WRAPAROUND HANDLING
Two different mechanisms, for two different comparisons:
  - Rule-vs-candidate (materialize_inf_rules): occurrences become real datetimes for
    the specific dates in play, so the existing datetime-based overlap logic (already
    correct for overnight spans) just works — no dateless math involved.
  - Infinite-vs-infinite (thin_schedule_dateless): stays entirely dateless, on
    purpose — merges the schedule, splits any wrapping existing rule into two
    non-wrapping pieces, sweep-subtracts, then fixes the ONE seam a left-to-right
    pass can't see (does the last surviving piece wrap around to meet the first?).
    Only the final survivors get resolved to a real date, once, at the very end
    (resolve_to_first_occurrence) — not every row up front.

SCHEMA (as explicitly decided): BookingSeries stores explicit local start/end datetimes
(dtstart, dtend) — weekday + time-of-day pairs are derived from these (.weekday(), .time()),
not stored separately, and there's no separate duration field (series.duration = dtend - dtstart).
This was a direct decision, not an assumption: keeps the overlap check simple to read
(WeekdayTime pairs) and gives an overnight infinite series an unambiguous end.

EDGE CASES

- Window boundary (resolve_schedule + gather_busy_slice):
  time_min arrives as UTC; converting to local (e.g. America/New_York, UTC-5) can
  land on an earlier calendar day (Jan 6 02:00 UTC → Jan 5 21:00 EST). Two things
  must both happen at this boundary:
  · resolve_schedule anchors to local_time_min (the earlier local day) so that any
    schedule window on that day — e.g. Jan 5 21:00–23:00 EST — is included as a
    candidate. Anchoring to the UTC date instead would miss those slots entirely.
  · gather_busy_slice always pulls one extra day before each range start so that a
    booking starting before time_min (e.g. Sun 23:00) but ending inside it (Mon
    00:30) is loaded into the busy check. Without this, Sun's bucket is missed and
    Mon 00:00 is wrongly offered as free.
  c_start = max(b_start, time_min) in generate_weekly_free_slots then ensures no
  slot is offered with a start time before the actual time_min.

- Midnight-crossing sessions (overnight spans):
  A session (e.g. 23:00–01:00) produces a block where end is on the next calendar
  day. Once resolved to a concrete datetime pair, the standard overlap check handles
  it correctly — no special-casing needed. The wrinkle is at resolve_schedule: the
  last block of the week (e.g. Sun 22:00–23:59) and first block (Mon 00:00–01:00)
  are semantically one continuous overnight window and are merged via the ≤1-min
  seam check.

- Sunday→Monday seam in dateless space (thin_schedule_dateless):
  After subtraction, survivors may include a Sun-end and a Mon-start that are
  adjacent. A left-to-right merge can't detect this (end key > start key check
  fails across the week boundary). Fix: post-subtraction, check if last_end ==
  first_start (or last_end=(6,23:59) and first_start=(0,00:00)) and insert the
  merged piece at the front.

- Seam piece in resolve_to_first_occurrence:
  A merged Sun→Mon piece has start_weekday=6. With time_min on Monday,
  days_ahead=(6-0)%7=6 → resolves to the NEXT Sunday, placing the Monday tail
  7 days past time_min. Fix: if b_end - 1 week >= time_min, step back one week —
  the seam piece already reaches into the window from the prior week.

RESCHEDULE SELF-EXCLUSION

Whatever is being rescheduled must not block itself, or it can't move into a slot
overlapping its own current time (4:00-5:30 -> 4:15-5:45). Its own exact slot is
withheld instead, since rescheduling to an identical time is a no-op.

The mode is chosen by the OPERATION, not just the event type: moving one occurrence is
a standalone booking (one date), not a claim on the weekly band, so exclude_ref forces
standalone mode. That's why no un-thinning is needed — thinning never runs.

TIMEZONE DESIGN

All schedule times, series patterns, and busy blocks are stored in the business
canonical timezone (Settings.business_timezone, default "America/New_York"). UTC is
not used for recurring patterns — UTC has no DST concept, so a slot stored as a UTC
time silently shifts wall-clock time by ±1hr across DST boundaries. With one canonical
timezone, all of a tutor's series always share the same DST regime: no gap or overlap
between adjacent lessons can appear when clocks change.

Schedule.timezone is currently stored but redundant — always equals Settings.business_timezone.
Nullable pending a refactor that replaces it with a single Settings load (BookingSeries dropped
its own timezone column already — dtstart/dtend are local values resolved via Settings directly,
no per-row timezone at all). Migration: on canonical timezone change, shift all series/schedule
times by the old→new offset (using .now() for the current DST state), then update Settings.

Display timezone is separate from storage timezone. The frontend converts canonical
times to the viewer's browser timezone (or a user-selected display timezone) for
display only. Stored values are never in the viewer's local time.

timedelta(days=7) advances inside run_finite / run_infinite act on datetimes carrying
ZoneInfo — they add to the naive component, which is DST-safe. Advances in bookings.py
generation loops must go through .astimezone(BUSINESS_TZ) + timedelta(days=7) +
.astimezone(UTC) to preserve wall-clock time across DST boundaries.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from booking_utils import active_series_filter
from database import get_db
from gcal import SCOPES, get_calendar_service
from models import Booking, BookingSeries, BookingLink, BookingLinkAvailability, Settings, Tutor
from schemas import AvailableSlotResponse

router = APIRouter(prefix="/available-slots", tags=["available-slots"])


# ============================================================
# TYPES
# ============================================================

class WeekdayTime:
    def __init__(self, weekday: int, time_of_day: time):
        self.weekday = weekday
        self.time_of_day = time_of_day


# an infinite rule: (start: WeekdayTime, end: WeekdayTime, dev_starts: set[date])
InfiniteRule = tuple[WeekdayTime, WeekdayTime, set[date]]
Block = tuple[datetime, datetime]


# ============================================================
# SHARED LEAF HELPERS
# ============================================================

def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def merge_dateless_pieces(pieces: list) -> list:
    # Merge sorted, directly-touching (start_key, end_key) pieces — same left-to-right
    # merge as resolve_schedule used to do with dates, just on (weekday, time) keys.
    # Deliberately does NOT glue the last piece onto the first here — that glue
    # would create a piece whose end key is numerically SMALLER than its start key
    # (e.g. Sun 10pm glued to Mon 3am -> ((6,22:00),(0,3:00))), which is a malformed
    # interval for anything downstream that assumes start < end (the sweep included).
    # The wraparound seam is checked later instead — see thin_schedule_dateless —
    # on the SURVIVORS after subtraction, not here on the raw merge.
    if not pieces:
        return []
    merged = []
    cur_start, cur_end = pieces[0]
    for (next_start, next_end) in pieces[1:]:
        if next_start == cur_end:
            cur_end = next_end
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = next_start, next_end
    merged.append((cur_start, cur_end))
    return merged


def split_if_wrapping(start_weekday: int, start_time: time, end_weekday: int, end_time: time) -> list:
    # An infinite rule CAN wrap (an existing series can span Sunday night into
    # Monday morning) — split it into two non-wrapping pieces so both sides of any
    # later comparison stay on a linear, non-wrapping key.
    start_key = (start_weekday, start_time)
    end_key = (end_weekday, end_time)
    if end_key <= start_key:
        return [(start_key, (6, time.max)), ((0, time.min), end_key)]
    return [(start_key, end_key)]


def subtract_intervals_sweep(free_pieces: list, busy_pieces: list) -> list:
    # Two-pointer sweep: subtract sorted busy_pieces from sorted free_pieces in one
    # pass, O(F + B) — not the O(F x B) a nested loop would give.
    result = []
    i, j = 0, 0
    cursor = None
    while i < len(free_pieces):
        f_start, f_end = free_pieces[i]
        if cursor is None:
            cursor = f_start
        while j < len(busy_pieces) and busy_pieces[j][1] <= cursor:
            j += 1
        if j >= len(busy_pieces) or busy_pieces[j][0] >= f_end:
            if cursor < f_end:
                result.append((cursor, f_end))
            i += 1
            cursor = None
            continue
        b_start, b_end = busy_pieces[j]
        if b_start > cursor:
            result.append((cursor, b_start))
        cursor = max(cursor, b_end)
        if cursor >= f_end:
            i += 1
            cursor = None
    return result


def thin_schedule_dateless(schedule_rows: list, inf_rules_for_tutor: list) -> list:
    # A new infinite series can never be booked where an EXISTING one already
    # recurs. Stays entirely in (weekday, time) space: raw schedule rows can be
    # discontinuous OR touching — no pre-merge needed, since subtraction handles
    # each row independently (a fresh cursor per piece). Split any wrapping
    # existing rule into non-wrapping pieces, sweep-subtract, THEN merge the
    # survivors (two originally-adjacent rows only stay merged if nothing got
    # carved out right at their shared boundary — a post-subtraction merge gets
    # this right without ever needing to pre-merge the raw rows), THEN fix the one
    # wraparound seam. Only resolve to a real date at the very end
    # (resolve_to_first_occurrence) — dates are picked exactly once, for the final
    # surviving pieces, not for every row up front.
    free_pieces = sorted(
        [((wd, st), (wd, et)) for (wd, st, et) in schedule_rows],
        key=lambda p: p[0],
    )

    busy_pieces = []
    for (rule_start, rule_end, _dev) in inf_rules_for_tutor:
        busy_pieces.extend(split_if_wrapping(
            rule_start.weekday, rule_start.time_of_day,
            rule_end.weekday, rule_end.time_of_day,
        ))
    busy_pieces.sort(key=lambda p: p[0])

    result = subtract_intervals_sweep(free_pieces, busy_pieces)
    result.sort(key=lambda p: p[0])

    # merge any survivors that are still adjacent post-subtraction
    result = merge_dateless_pieces(result)

    # NOW check the wraparound seam — does whatever survived of the original last
    # piece still touch whatever survived of the original first piece? Only safe
    # to check here, on well-formed (start < end) survivors, never on a glued,
    # potentially-backwards piece the way an earlier version of this attempted.
    if len(result) > 1:
        first_start, first_end = result[0]
        last_start, last_end = result[-1]
        seam = last_end == first_start or (
            last_end == (6, time(23, 59)) and first_start == (0, time(0, 0))
        )
        if seam:
            result.pop(0)
            result.pop(-1)
            result.insert(0, (last_start, first_end))

    return result


def resolve_to_first_occurrence(piece: tuple, time_min: datetime) -> tuple:
    # The only place a real date gets picked in the dateless-thinning path — once,
    # for a surviving (start_key, end_key) pattern, converting it to its first
    # concrete occurrence on/after time_min.
    (start_weekday, start_time), (end_weekday, end_time) = piece
    days_ahead = (start_weekday - time_min.date().weekday()) % 7
    first_date = time_min.date() + timedelta(days=days_ahead)
    day_offset = (end_weekday - start_weekday) % 7
    b_start = datetime.combine(first_date, start_time, tzinfo=time_min.tzinfo)
    b_end = datetime.combine(first_date + timedelta(days=day_offset), end_time, tzinfo=time_min.tzinfo)
    # seam pieces (Sun→Mon overnight) resolve 6 days ahead, putting the Monday
    # tail a week past time_min — step back one week if b_end still reaches time_min
    candidate_end = b_end - timedelta(weeks=1)
    if candidate_end >= time_min:
        return (b_start - timedelta(weeks=1), candidate_end)
    return (b_start, b_end)


def materialize_inf_rules(inf_rules_for_tutor: list, positions: list, tz: ZoneInfo) -> list:
    # TODO: date walking is redundant — same-day positions each re-walk the same dates and re-check
    # every rule, and the -1 day pull adds prior-day occurrences irrelevant to the actual candidates.
    # Harmless now (few rules/positions per tutor).
    # Materialize each active infinite rule's occurrences into real (start, end)
    # datetimes, for the dates THIS batch's positions actually touch — not to any
    # horizon. This is what removes the wraparound problem for rule-vs-candidate
    # checks entirely: once an occurrence is a real datetime, the existing
    # datetime-based overlap logic (already correct for overnight spans) just works.
    # Deviated dates are skipped here (holes by omission), so no separate filtering
    # step is needed downstream.
    materialized = []
    for (range_start, range_end) in positions:
        d = range_start.date() - timedelta(days=1)  # match gather_busy_slice's own -1 day pull
        end_d = range_end.date()
        while d <= end_d:
            for (rule_start, rule_end, dev_starts) in inf_rules_for_tutor:
                if d.weekday() == rule_start.weekday and d not in dev_starts:
                    occ_start = datetime.combine(d, rule_start.time_of_day, tzinfo=tz)
                    day_offset = (rule_end.weekday - rule_start.weekday) % 7
                    occ_end = datetime.combine(d + timedelta(days=day_offset), rule_end.time_of_day, tzinfo=tz)
                    materialized.append((occ_start, occ_end))
            d += timedelta(days=1)
    return materialized


def drop_busy_slot_conflicts(candidate_slots: list, busy_slice: list) -> list:
    candidate_slots = sorted(candidate_slots, key=lambda c: c[0])
    busy_slice = sorted(busy_slice, key=lambda b: b[0])
    survivors = []
    i, j = 0, 0
    while i < len(candidate_slots):
        cand_start, cand_end = candidate_slots[i]
        while j < len(busy_slice) and busy_slice[j][1] <= cand_start:
            j += 1
        if j < len(busy_slice) and overlaps(cand_start, cand_end, *busy_slice[j]):
            pass  # dies here
        else:
            survivors.append((cand_start, cand_end))
        i += 1
    return survivors


# ============================================================
# SHARED SETUP HELPERS
# ============================================================

def _first_date_on_weekday(target_weekday: int, from_date: date) -> date:
    days_ahead = (target_weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def resolve_schedule(schedule, time_min: datetime) -> list:
    # resolve each schedule row to its first concrete occurrence on/after time_min
    raw_blocks = []
    for row in schedule.days:
        first_date = _first_date_on_weekday(row.day_of_week, time_min.date())
        raw_blocks.append((
            datetime.combine(first_date, row.start_time, tzinfo=time_min.tzinfo),
            datetime.combine(first_date, row.end_time, tzinfo=time_min.tzinfo),
        ))
    raw_blocks.sort(key=lambda b: b[0])

    # merge blocks that touch or are ≤1 min apart — 23:59 end + 00:00 start on the
    # next day are semantically continuous (inclusive blocks), not a real gap
    merged_blocks = []
    current_start, current_end = raw_blocks[0]
    for (next_start, next_end) in raw_blocks[1:]:
        if next_start <= current_end + timedelta(minutes=1):
            current_end = max(current_end, next_end)
        else:
            merged_blocks.append((current_start, current_end))
            current_start, current_end = next_start, next_end
    merged_blocks.append((current_start, current_end))

    # fix the wraparound seam a left-to-right merge can't see (last block onto first)
    first_start, first_end = merged_blocks[0]
    last_start, last_end = merged_blocks[-1]
    rolled_start = last_start - timedelta(days=7)
    rolled_end = last_end - timedelta(days=7)
    if rolled_end == first_start:
        merged_blocks.pop(0)
        merged_blocks.pop(-1)
        merged_blocks.append((rolled_start, first_end))

    return merged_blocks


def generate_weekly_free_slots(positions: list, time_min: datetime, time_max: datetime,
                                duration: timedelta, interval: timedelta) -> list:
    # time_max edge: a candidate is only ever produced if its END fits inside time_max —
    # nothing can spill past the window's close.
    candidate_slots = []
    for (b_start, b_end) in positions:
        c_start = max(b_start, time_min)
        while c_start + duration <= min(b_end, time_max):
            candidate_slots.append((c_start, c_start + duration))
            c_start += interval
    return candidate_slots


def merge_continuous_slots(candidate_slots: list) -> list:
    # merge sorted, touching/overlapping candidate_slots into contiguous ranges
    candidate_slots = sorted(candidate_slots, key=lambda c: c[0])
    ranges = []
    current_start, current_end = candidate_slots[0]
    for (s, e) in candidate_slots[1:]:
        if s <= current_end:
            current_end = max(current_end, e)
        else:
            ranges.append((current_start, current_end))
            current_start, current_end = s, e
    ranges.append((current_start, current_end))
    return ranges


def gather_busy_slice(busy_dict_for_tutor: dict, positions: list) -> list:
    # time_min edge: a real booking can start the night BEFORE the range and spill into
    # it. Always pull one extra day before each range's start, unconditionally.
    busy_slice = []
    for (b_s, b_e) in positions:
        d = b_s.date() - timedelta(days=1)
        end_d = b_e.date()
        while d <= end_d:
            busy_slice.extend(busy_dict_for_tutor.get(d, []))
            d += timedelta(days=1)
    return busy_slice


def merge_freebusy_into(busy_dict_for_tutor: dict, tutor, fb_min: datetime, fb_max: datetime,
                         calendar_service, tz: ZoneInfo) -> None:
    if not tutor.check_calendar_conflicts or not tutor.calendar_id or calendar_service is None:
        return
    try:
        result = calendar_service.freebusy().query(body={
            "timeMin": fb_min.isoformat(),
            "timeMax": fb_max.isoformat(),
            "items": [{"id": tutor.calendar_id}],
        }).execute()
    except Exception as e:
        logging.warning(f"freebusy query failed for tutor {tutor.id}: {e}")
        return

    for block in result.get("calendars", {}).get(tutor.calendar_id, {}).get("busy", []):
        fb_start = datetime.fromisoformat(block["start"]).astimezone(tz)
        fb_end = datetime.fromisoformat(block["end"]).astimezone(tz)
        busy_dict_for_tutor.setdefault(fb_start.date(), []).append((fb_start, fb_end))

    for d in busy_dict_for_tutor:
        busy_dict_for_tutor[d].sort(key=lambda b: b[0])


# NOTE: deviations are filtered per-exact-date, inline, inside materialize_inf_rules
# (`d not in dev_starts`) — never against one shared anchor date for a whole batch.
# A batch's candidate_slots can span MULTIPLE weekdays (free_blocks can hold a Monday
# block, a Wednesday block, etc.), so any single-date pre-filter would risk wrongly
# keeping or dropping a rule for a weekday it doesn't even apply to. Checking exactly
# per materialized date avoids that class of bug entirely.


# ============================================================
# MODE-SPECIFIC RUNNERS
# ============================================================

def run_standalone(free_blocks: list, tutor_id: int, time_min: datetime, time_max: datetime,
                    duration: timedelta, interval: timedelta,
                    busy_dict: dict, inf_rules: dict, tz: ZoneInfo) -> list:
    results = []
    weekly_free_blocks = free_blocks[:]

    # advance one occurrence at a time, 7 days per pass, until past the viewing window
    while any(b_start <= time_max for (b_start, _) in weekly_free_blocks):

        # generate every candidate slot across all free_blocks at once
        free_slots = generate_weekly_free_slots(weekly_free_blocks, time_min, time_max, duration, interval)
        if not free_slots:
            weekly_free_blocks = [(b_s + timedelta(days=7), b_e + timedelta(days=7)) for (b_s, b_e) in weekly_free_blocks]
            continue

        busy_slice = gather_busy_slice(busy_dict[tutor_id], weekly_free_blocks)
        busy_slice += materialize_inf_rules(inf_rules[tutor_id], weekly_free_blocks, tz)
        filtered_free_slots = drop_busy_slot_conflicts(free_slots, busy_slice)

        # standalone has no recurrence — whatever survives is free
        results.extend((tutor_id, s, e) for (s, e) in filtered_free_slots)

        weekly_free_blocks = [(b_s + timedelta(days=7), b_e + timedelta(days=7)) for (b_s, b_e) in weekly_free_blocks]
    return results


def run_finite(free_blocks: list, tutor_id: int, time_min: datetime, time_max: datetime,
               duration: timedelta, interval: timedelta, recur_weeks, expires_on,
               busy_dict: dict, inf_rules: dict, tz: ZoneInfo) -> list:
    results = []
    weekly_free_blocks = free_blocks[:]

    while any(b_start <= time_max for (b_start, _) in weekly_free_blocks):
        free_slots = generate_weekly_free_slots(weekly_free_blocks, time_min, time_max, duration, interval)
        weeks_checked = 0
        filtered_free_slots = []

        # sweep the shrinking batch week by week — must survive EVERY future occurrence
        while free_slots:
            ranges = merge_continuous_slots(free_slots)
            busy_slice = gather_busy_slice(busy_dict[tutor_id], ranges)
            busy_slice += materialize_inf_rules(inf_rules[tutor_id], ranges, tz)
            filtered_free_slots = drop_busy_slot_conflicts(free_slots, busy_slice)
            if not filtered_free_slots:
                break  # everyone died this week

            weeks_checked += 1
            earliest_date = filtered_free_slots[0][0].date()

            # stop at a fixed end date, or after a fixed number of occurrences
            if expires_on is not None:
                if earliest_date + timedelta(days=7) > expires_on:
                    break
            else:
                if weeks_checked >= recur_weeks:
                    break

            # shift forward to check next week's busy data; undone once at the end
            shifted_free_slots = [(s + timedelta(days=7), e + timedelta(days=7)) for (s, e) in filtered_free_slots]
            free_slots = shifted_free_slots

        # shift back to the original bookable time — filtered_free_slots holds survivors
        # from the last successful check, shifted (weeks_checked-1) times forward
        total_shift = timedelta(days=7 * (weeks_checked - 1)) if weeks_checked > 0 else timedelta(0)
        results.extend((tutor_id, s - total_shift, e - total_shift) for (s, e) in filtered_free_slots)

        weekly_free_blocks = [(b_s + timedelta(days=7), b_e + timedelta(days=7)) for (b_s, b_e) in weekly_free_blocks]
    return results


def run_infinite(free_blocks: list, tutor_id: int, time_min: datetime, time_max: datetime,
                  duration: timedelta, interval: timedelta, horizon: datetime,
                  busy_dict: dict, inf_rules: dict, tz: ZoneInfo) -> list:
    results = []
    weekly_free_blocks = free_blocks[:]

    while any(b_start <= time_max for (b_start, _) in weekly_free_blocks):
        # free_blocks already had any colliding-forever ranges thinned out before this
        # runner was even called (see thin_schedule_dateless) — no per-candidate
        # guard needed here.
        free_slots = generate_weekly_free_slots(weekly_free_blocks, time_min, time_max, duration, interval)
        weeks_checked = 0
        filtered_free_slots = []

        # same shrinking-batch sweep as finite, stopping at the tutor's horizon instead
        while free_slots:
            ranges = merge_continuous_slots(free_slots)
            busy_slice = gather_busy_slice(busy_dict[tutor_id], ranges)
            # inf_rules already subtracted from free_blocks via thin_schedule_dateless
            # before this runner was called — no need to materialize them again here
            filtered_free_slots = drop_busy_slot_conflicts(free_slots, busy_slice)
            if not filtered_free_slots:
                break

            weeks_checked += 1
            if filtered_free_slots[0][0] + timedelta(days=7) > horizon:
                break  # nothing finite exists past this point

            shifted_free_slots = [(s + timedelta(days=7), e + timedelta(days=7)) for (s, e) in filtered_free_slots]
            free_slots = shifted_free_slots

        total_shift = timedelta(days=7 * (weeks_checked - 1)) if weeks_checked > 0 else timedelta(0)
        results.extend((tutor_id, s - total_shift, e - total_shift) for (s, e) in filtered_free_slots)

        weekly_free_blocks = [(b_s + timedelta(days=7), b_e + timedelta(days=7)) for (b_s, b_e) in weekly_free_blocks]
    return results


# ============================================================
# ENTRY POINT
# ============================================================

def get_available_slots(
    tutor_ids: list,
    booking_link_id: int,
    time_min: datetime,  # UTC
    time_max: datetime,  # UTC
    db,
    calendar_service=None,
    exclude_ref: str | None = None,         # public_id of the booking being rescheduled
    exclude_series_ref: str | None = None,  # public_id of the series being rescheduled
) -> list:

    db_tutors = db.query(Tutor).filter(Tutor.id.in_(tutor_ids), Tutor.is_active == True).all()
    db_booking_link = db.query(BookingLink).filter(BookingLink.id == booking_link_id).first()

    availability_by_tutor = {
        a.tutor_id: a.schedule
        for a in db.query(BookingLinkAvailability).filter(
            BookingLinkAvailability.booking_link_id == booking_link_id,
            BookingLinkAvailability.tutor_id.in_(tutor_ids),
        ).all()
    }
    _settings = db.query(Settings).filter(Settings.id == 1).first()
    if _settings is None:
        raise HTTPException(status_code=500, detail="Settings not initialised — run the setup flow first")
    # business canonical timezone — all schedule/series times are stored in this zone
    business_tz = ZoneInfo(_settings.business_timezone)

    # See RESCHEDULE SELF-EXCLUSION in the module docstring.
    # exclude_booking_id  a real Booking row to drop from busy_dict
    # exclude_hole        (series_id, date) — opens one date of a series' band. Read off the ref,
    #                     which encodes its own start ("{series_public_id}:{unix_timestamp}"), so
    #                     this works for a virtual occurrence with no row at all.
    # exclude_span        the exact slot to withhold from the results
    exclude_booking_id = exclude_hole = exclude_span = None
    if exclude_ref is not None:
        row = db.query(Booking).filter(Booking.public_id == exclude_ref).first()
        if row is not None:
            exclude_booking_id = row.id
            r_start = row.start if row.start.tzinfo else row.start.replace(tzinfo=UTC)
            r_end = row.end if row.end.tzinfo else row.end.replace(tzinfo=UTC)
            exclude_span = (r_start.astimezone(business_tz), r_end.astimezone(business_tz))
        series_pub, sep, ts = exclude_ref.rpartition(":")
        if sep and ts.isdigit():
            s = db.query(BookingSeries).filter(BookingSeries.public_id == series_pub).first()
            if s is not None:
                occ_start = datetime.fromtimestamp(int(ts), UTC).astimezone(business_tz)
                exclude_hole = (s.id, occ_start.date())
                if exclude_span is None:  # virtual occurrence — span comes from the series
                    exclude_span = (occ_start, occ_start + (s.dtend - s.dtstart))

    exclude_series_id = exclude_series_pattern = None
    if exclude_series_ref is not None:
        s = db.query(BookingSeries).filter(BookingSeries.public_id == exclude_series_ref).first()
        if s is not None:
            exclude_series_id = s.id
            exclude_series_pattern = (s.dtstart.weekday(), s.dtstart.time(), s.dtend.time())

    duration = timedelta(minutes=db_booking_link.duration_minutes)
    interval = timedelta(minutes=db_booking_link.interval_minutes or db_booking_link.duration_minutes)
    expires_on = db_booking_link.expires_on
    recur_weeks = db_booking_link.recur_weeks

    if not db_booking_link.recurring or exclude_ref is not None:
        # exclude_ref means one occurrence is moving — one date, not a recurring claim
        mode = "standalone"
    elif expires_on is not None or recur_weeks is not None:
        mode = "finite"
    else:
        mode = "infinite"

    # horizon: fixed for standalone/finite. None for infinite — that horizon depends on
    # how far out each TUTOR'S OWN busy data reaches, computed per-tutor once busy_dict
    # is loaded, in the per-tutor loop below.
    if mode == "standalone":
        horizon = time_max
    elif mode == "finite":
        if expires_on is not None:
            horizon = datetime.combine(expires_on + timedelta(days=1), time.min, tzinfo=UTC)
        else:
            horizon = datetime.combine(
                time_max.date() + timedelta(weeks=recur_weeks - 1, days=1), time.min, tzinfo=UTC
            )
    else:
        horizon = None

    # query bounds: pull one extra day before time_min AND after horizon. The lower
    # pad catches a booking starting the night before time_min and spilling in. The
    # upper pad is ALSO needed (not just lower) — an overnight-crossing candidate can
    # start just before horizon but END after it (e.g. horizon = midnight Jan 11,
    # candidate starts Jan 10 11pm, 3hr duration -> ends Jan 11 2am). A real booking
    # starting Jan 11 1am would genuinely overlap that candidate but starts >= horizon,
    # so it would be wrongly excluded without this pad. Infinite's query stays
    # unbounded above — its horizon isn't known until this query returns.
    query_min = time_min - timedelta(days=1)
    query_max = (horizon + timedelta(days=1)) if horizon is not None else None

    # confirmed bookings -> busy_dict, converted to each tutor's OWN LOCAL TIMEZONE.
    # This is what lets busy_dict compare directly against the tutor's date-naive
    # schedule and infinite rules, which are inherently local, not UTC, concepts.
    booking_q = (
        db.query(Booking)
        .filter(
            Booking.tutor_id.in_(tutor_ids),
            Booking.status == "confirmed",
            Booking.start >= query_min,
        )
        .order_by(Booking.start)
    )
    if exclude_booking_id is not None:
        booking_q = booking_q.filter(Booking.id != exclude_booking_id)
    if exclude_series_id is not None:
        # the series' already-materialized occurrences would otherwise still block its own band
        booking_q = booking_q.filter(Booking.series_id != exclude_series_id)
    if query_max is not None:
        booking_q = booking_q.filter(Booking.start < query_max)

    busy_dict = {t.id: {} for t in db_tutors}
    for b in booking_q.all():
        b_start_utc = b.start if b.start.tzinfo else b.start.replace(tzinfo=UTC)
        b_end_utc = b.end if b.end.tzinfo else b.end.replace(tzinfo=UTC)
        b_start_local = b_start_utc.astimezone(business_tz)
        b_end_local = b_end_utc.astimezone(business_tz)
        busy_dict[b.tutor_id].setdefault(b_start_local.date(), []).append((b_start_local, b_end_local))
    # rows arrive sorted (ORDER BY start) so each date's bucket is already sorted

    # active infinite series -> inf_rules, a FLAT list per tutor (each rule already
    # encodes its own weekday via WeekdayTime, so no need to bucket by weekday — and
    # candidates in one pass can span multiple weekdays anyway). Holes come from any
    # of the series' own occurrences that were cancelled/rescheduled.
    inf_rules = {t.id: [] for t in db_tutors}
    today = datetime.now(business_tz).date()
    series_q = db.query(BookingSeries).filter(
        BookingSeries.tutor_id.in_(tutor_ids),
        active_series_filter(today),
        BookingSeries.until == None,
    )
    if exclude_series_id is not None:
        series_q = series_q.filter(BookingSeries.id != exclude_series_id)
    for series in series_q.all():
        dev_starts = set()
        for booked in series.bookings:
            if booked.status in ("cancelled", "rescheduled"):
                dev_start_utc = booked.start if booked.start.tzinfo else booked.start.replace(tzinfo=UTC)
                dev_starts.add(dev_start_utc.astimezone(business_tz).date())
        if exclude_hole is not None and exclude_hole[0] == series.id:
            dev_starts.add(exclude_hole[1])

        rule_start = WeekdayTime(series.dtstart.weekday(), series.dtstart.time())
        rule_end = WeekdayTime(series.dtend.weekday(), series.dtend.time())
        inf_rules[series.tutor_id].append((rule_start, rule_end, dev_starts))

    # freebusy merged upfront for standalone/finite — infinite needs its own per-tutor
    # horizon first, so its freebusy merge happens later, inside the per-tutor loop
    if mode in ("standalone", "finite"):
        for tutor in db_tutors:
            merge_freebusy_into(
                busy_dict[tutor.id], tutor, query_min,
                (horizon + timedelta(days=1)) if horizon is not None else time_max + timedelta(days=1),
                calendar_service, business_tz,
            )

    all_slots = []

    for tutor in db_tutors:
        schedule = availability_by_tutor.get(tutor.id)
        if schedule is None:
            continue

        local_time_min = time_min.astimezone(business_tz)

        if mode == "infinite":
            # per-tutor horizon: how far THIS tutor's own busy commitments reach,
            # floored at 30 days out so a tutor with nothing booked yet is still sane
            all_ends = [end for blocks in busy_dict[tutor.id].values() for (_, end) in blocks]
            tutor_horizon = max(max(all_ends, default=datetime.now(UTC)), datetime.now(UTC) + timedelta(days=30))
            merge_freebusy_into(
                busy_dict[tutor.id], tutor, query_min, tutor_horizon + timedelta(days=1),
                calendar_service, business_tz,
            )
            # carve out any range where an EXISTING infinite series already recurs —
            # entirely dateless, before any real date is picked at all
            schedule_rows = [(row.day_of_week, row.start_time, row.end_time) for row in schedule.days]
            dateless_pieces = thin_schedule_dateless(schedule_rows, inf_rules[tutor.id])
            free_blocks = [resolve_to_first_occurrence(p, local_time_min) for p in dateless_pieces]
        else:
            free_blocks = resolve_schedule(schedule, local_time_min)
            tutor_horizon = horizon

        if mode == "standalone":
            tutor_slots = run_standalone(free_blocks, tutor.id, time_min, time_max, duration, interval,
                                          busy_dict, inf_rules, business_tz)
        elif mode == "finite":
            tutor_slots = run_finite(free_blocks, tutor.id, time_min, time_max, duration, interval,
                                      recur_weeks, expires_on, busy_dict, inf_rules, business_tz)
        else:
            tutor_slots = run_infinite(free_blocks, tutor.id, time_min, time_max, duration, interval,
                                       tutor_horizon, busy_dict, inf_rules, business_tz)

        all_slots.extend(tutor_slots)

    # withhold the excluded thing's own slot — rescheduling to an identical time is a no-op
    if exclude_span is not None:
        ex_s, ex_e = exclude_span
        all_slots = [(t, s, e) for (t, s, e) in all_slots if not (s == ex_s and e == ex_e)]
    if exclude_series_pattern is not None:
        ex_wd, ex_st, ex_et = exclude_series_pattern  # a series' identity is its weekly pattern
        all_slots = [(t, s, e) for (t, s, e) in all_slots
                     if not (s.weekday() == ex_wd and s.time() == ex_st and e.time() == ex_et)]

    return [AvailableSlotResponse(tutor_id=t, start=s, end=e) for (t, s, e) in all_slots]


@router.get("/", response_model=list[AvailableSlotResponse])
def available_slots_endpoint(
    tutor_ids: list[int] = Query(),
    booking_link_id: int = Query(),
    time_min: datetime = Query(),
    time_max: datetime = Query(),
    exclude_ref: str | None = Query(default=None),         # booking being rescheduled
    exclude_series_ref: str | None = Query(default=None),  # series being rescheduled
    db: Session = Depends(get_db),
):
    db_tutors = db.query(Tutor).filter(Tutor.id.in_(tutor_ids)).all()
    missing = set(tutor_ids) - {t.id for t in db_tutors}
    if missing:
        raise HTTPException(status_code=404, detail=f"Tutors not found: {missing}")
    # Checked here, not inside get_available_slots — that stays a pure slot algorithm.
    # Archived only: a paused link still serves reschedules, and its new-booking attempt is
    # blocked authoritatively by create_booking.
    if not db.query(BookingLink).filter(
        BookingLink.id == booking_link_id, BookingLink.status != "archived"
    ).first():
        raise HTTPException(status_code=404, detail="Booking link not found")
    if time_min.tzinfo is None:
        time_min = time_min.replace(tzinfo=UTC)
    if time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=UTC)
    try:
        calendar_service = get_calendar_service(SCOPES)
    except Exception:
        calendar_service = None
    try:
        return get_available_slots(tutor_ids, booking_link_id, time_min, time_max, db, calendar_service,
                                   exclude_ref=exclude_ref, exclude_series_ref=exclude_series_ref)
    except Exception as e:
        logging.error(f"available_slots failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to compute available slots")