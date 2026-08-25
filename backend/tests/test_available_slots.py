"""
Tests for GET /available-slots.

Covers all three modes (standalone, finite, infinite) and key edge cases:
midnight-crossing rules, Sunday→Monday wrap via split_if_wrapping, multi-tutor
independence, deviation holes, and finite-vs-infinite series distinction.

Setup strategy: tutors/schedules/event_types are created via the HTTP client
(same as other test files). BookingSeries and Booking rows that need precise
schema control (e.g. exact dtstart/dtend, including midnight-crossing cases)
are inserted directly via a db session that shares the same SQLite file as the client.

June 2099 starts on a Monday: 2099-06-07=Sun, 2099-06-08=Mon, 2099-06-10=Wed.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Booking, BookingSeries, Settings

# ── Shared DB session (same file as conftest.py client fixture) ───────────────

_engine = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
_TestingSessionLocal = sessionmaker(bind=_engine)

MON = date(2099, 6, 8)   # Monday  (weekday=0)
SUN = date(2099, 6, 7)   # Sunday  (weekday=6)
WED = date(2099, 6, 10)  # Wednesday (weekday=2)


def _dt(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _at(d: date, h: int, m: int = 0) -> str:
    """Prefix of an ISO datetime string — robust to +00:00 vs Z formatting."""
    return f"{d}T{h:02d}:{m:02d}:"


def _params(tutor_ids, event_type_id: int, d_min: date, d_max: date) -> dict:
    ids = [tutor_ids] if isinstance(tutor_ids, int) else list(tutor_ids)
    return {
        "tutor_ids": ids,
        "event_type_id": event_type_id,
        "time_min": f"{d_min}T00:00:00Z",
        "time_max": f"{d_max}T23:59:00Z",
    }


def _has_start(slots: list, d: date, h: int, m: int = 0) -> bool:
    prefix = _at(d, h, m)
    return any(s["start"].startswith(prefix) for s in slots)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(client):
    """Direct ORM session on the same SQLite file. Depends on client so
    create_all runs before we insert anything."""
    session = _TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── Setup helpers ─────────────────────────────────────────────────────────────

def _tutor(client) -> dict:
    return client.post("/tutors/", json={"first_name": "T", "last_name": "T", "pay_rate": 0}).json()


def _schedule(client, tutor_id: int, days: list, timezone: str = "UTC") -> dict:
    return client.post("/schedules/", json={
        "tutor_id": tutor_id, "name": "Default", "is_default": True,
        "timezone": timezone, "days": days,
    }).json()


def _event_type(client, availability: list, **kwargs) -> dict:
    return client.post("/event_types/", json={
        "name": f"ET-{uuid4().hex[:6]}",
        "duration_minutes": 90,
        "recurring": False,
        **kwargs,
        "availability": availability,
    }).json()


def _avail(tutor_id: int, schedule_id: int) -> list:
    return [{"tutor_id": tutor_id, "schedule_id": schedule_id}]


def _mon_9_17(client, tutor_id: int) -> dict:
    """UTC schedule: Mon 09:00–17:00."""
    return _schedule(client, tutor_id, [{"day_of_week": 0, "start_time": "09:00:00", "end_time": "17:00:00"}])


def _insert_series(db, tutor_id: int, event_type_id: int, *,
                   start_dow: int, start_t: time,
                   end_dow: int, end_t: time,
                   until: date | None = None) -> BookingSeries:
    start_date = MON + timedelta(days=start_dow)  # value doesn't matter to available_slots
    end_date = start_date + timedelta(days=(end_dow - start_dow) % 7)  # wraps forward for midnight-crossing cases
    s = BookingSeries(
        tutor_id=tutor_id, event_type_id=event_type_id,
        dtstart=datetime.combine(start_date, start_t),
        dtend=datetime.combine(end_date, end_t),
        until=until,
        google_event_id=str(uuid4()),
        student_first="A", student_last="B",
        student_email="a@b.com", student_phone="555-0000",
    )
    db.add(s)
    db.flush()
    return s


def _insert_booking(db, tutor_id: int, event_type_id: int,
                    start: datetime, end: datetime, *,
                    series: BookingSeries | None = None,
                    status: str = "confirmed") -> Booking:
    b = Booking(
        series_id=series.id if series else None,
        tutor_id=tutor_id, event_type_id=event_type_id,
        start=start, end=end,
        google_event_id=str(uuid4()), status=status,
        timezone="UTC",
        student_first="A", student_last="B",
        student_email="a@b.com", student_phone="555-0000",
    )
    db.add(b)
    db.commit()
    return b


# ── HTTP validation ───────────────────────────────────────────────────────────

def test_unknown_tutor_returns_404(client):
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))
    r = client.get("/available-slots/", params=_params(9999, et["id"], MON, MON))
    assert r.status_code == 404


def test_unknown_event_type_returns_404(client):
    tutor = _tutor(client)
    _mon_9_17(client, tutor["id"])
    r = client.get("/available-slots/", params=_params(tutor["id"], 9999, MON, MON))
    assert r.status_code == 404


# ── Standalone ────────────────────────────────────────────────────────────────

def test_standalone_slot_count_and_boundaries(client):
    """Mon 09:00–17:00, 90-min duration+interval → exactly 5 slots.
    Last valid start is 15:00 (15:00+90=16:30 ≤ 17:00); 16:30+90=18:00 is outside."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert len(slots) == 5
    assert all(s["tutor_id"] == tutor["id"] for s in slots)
    assert _has_start(slots, MON, 9)           # first slot
    assert _has_start(slots, MON, 15)          # last valid start
    assert not _has_start(slots, MON, 16, 30)  # would end at 18:00 — outside window


def test_standalone_empty_when_window_misses_schedule_day(client):
    """Schedule is Mon-only; querying Wednesday returns nothing."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], WED, WED)).json()
    assert slots == []


def test_standalone_confirmed_booking_blocks_overlapping_slot(client, db):
    """Existing confirmed booking at 10:30–12:00 removes that slot; others survive."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))
    _insert_booking(db, tutor["id"], et["id"], _dt(MON, 10, 30), _dt(MON, 12))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert len(slots) == 4
    assert not _has_start(slots, MON, 10, 30)  # blocked
    assert _has_start(slots, MON, 9)            # before conflict → free
    assert _has_start(slots, MON, 12)           # after conflict → free


def test_standalone_cancelled_booking_does_not_block(client, db):
    """Cancelled bookings are excluded from busy_dict — slot stays available."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))
    _insert_booking(db, tutor["id"], et["id"], _dt(MON, 10, 30), _dt(MON, 12), status="cancelled")

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()
    assert len(slots) == 5  # all 5 — cancelled booking is invisible to busy_dict


def test_standalone_multi_day_schedule(client):
    """Mon + Wed schedule; window Mon–Wed → slots land on both days."""
    tutor = _tutor(client)
    sched = _schedule(client, tutor["id"], [
        {"day_of_week": 0, "start_time": "09:00:00", "end_time": "12:00:00"},
        {"day_of_week": 2, "start_time": "09:00:00", "end_time": "12:00:00"},
    ])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, WED)).json()

    dates = {s["start"][:10] for s in slots}
    assert str(MON) in dates
    assert str(WED) in dates


# ── Finite ────────────────────────────────────────────────────────────────────

def test_finite_slot_clear_across_all_recur_weeks(client):
    """recur_weeks=3, no conflicts → slots returned for the starting day."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True, recur_weeks=3)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()
    assert len(slots) > 0
    assert all(s["tutor_id"] == tutor["id"] for s in slots)


def test_finite_slot_blocked_by_conflict_in_week_2(client, db):
    """Slot is free week 1 but conflicts in week 2 → eliminated before week 1 is returned.
    The 09:00 slot is gone; 10:30 (which is free in all 3 weeks) survives."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True, recur_weeks=3)
    _insert_booking(db, tutor["id"], et["id"],
                    _dt(MON + timedelta(weeks=1), 9),
                    _dt(MON + timedelta(weeks=1), 10, 30))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert not _has_start(slots, MON, 9)       # blocked: week-2 occurrence is taken
    assert _has_start(slots, MON, 10, 30)      # clear in all 3 weeks → survives


def test_finite_slot_blocked_by_conflict_in_week_3(client, db):
    """Conflict only in week 3 (the last checked week) still eliminates the slot."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True, recur_weeks=3)
    _insert_booking(db, tutor["id"], et["id"],
                    _dt(MON + timedelta(weeks=2), 9),
                    _dt(MON + timedelta(weeks=2), 10, 30))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert not _has_start(slots, MON, 9)
    assert _has_start(slots, MON, 10, 30)


# ── Infinite ──────────────────────────────────────────────────────────────────

def test_infinite_returns_slots_with_no_competing_series(client):
    """No existing infinite series → standard schedule slots available."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()
    assert len(slots) > 0


def test_infinite_existing_series_thins_schedule_at_its_span(client, db):
    """Existing infinite series at Mon 09:00–10:30 is carved out of the schedule
    by thin_schedule_dateless before any date is picked. The 09:00 slot disappears;
    10:30 onward is untouched."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True)

    series = _insert_series(db, tutor["id"], et["id"],
                            start_dow=0, start_t=time(9, 0),
                            end_dow=0, end_t=time(10, 30))
    _insert_booking(db, tutor["id"], et["id"], _dt(MON, 9), _dt(MON, 10, 30), series=series)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert not _has_start(slots, MON, 9)       # thinned out — series occupies this forever
    assert _has_start(slots, MON, 10, 30)      # right after the rule's end → free


def test_infinite_finite_series_does_not_thin_schedule(client, db):
    """Series with until set is excluded from inf_rules — it doesn't thin
    the schedule, so those time ranges remain bookable for a new infinite series."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True)

    # finite series (until in the past — completely done)
    series = _insert_series(db, tutor["id"], et["id"],
                            start_dow=0, start_t=time(9, 0),
                            end_dow=0, end_t=time(10, 30),
                            until=date(2020, 1, 1))
    _insert_booking(db, tutor["id"], et["id"],
                    _dt(date(2020, 1, 6), 9), _dt(date(2020, 1, 6), 10, 30), series=series)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    # finite series doesn't appear in inf_rules → Mon 09:00 is still free
    assert _has_start(slots, MON, 9)


def test_infinite_cancelled_occurrence_is_a_deviation_but_rule_still_thins(client, db):
    """A cancelled occurrence adds a dev_start to the rule. thin_schedule_dateless
    operates at the dateless (weekday, time) level and has no knowledge of deviations —
    the rule still carves out that span. The hole only matters in materialize_inf_rules
    (prevents a one-off busy block that week), not in whether new infinite series can go there."""
    tutor = _tutor(client)
    sched = _mon_9_17(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True)

    series = _insert_series(db, tutor["id"], et["id"],
                            start_dow=0, start_t=time(9, 0),
                            end_dow=0, end_t=time(10, 30))
    _insert_booking(db, tutor["id"], et["id"], _dt(MON, 9), _dt(MON, 10, 30),
                    series=series, status="cancelled")

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    # Rule still active and infinite → still thinned at dateless level
    assert not _has_start(slots, MON, 9)


def test_infinite_midnight_crossing_rule_split_correctly(client, db):
    """Existing infinite series spans Sunday 23:00 → Monday 01:00.
    split_if_wrapping splits it into [Sun 23:00, Sun max] + [Mon min, Mon 01:00].
    Both halves are subtracted from their respective schedule blocks.

    Schedule: Sun 22:00–23:59 + Mon 00:00–02:00 (60-min event type).
    After thinning:
      Sun survivors: 22:00–23:00 (23:00–23:59 carved out by the Sun half of the rule)
      Mon survivors: 01:00–02:00 (00:00–01:00 carved out by the Mon half of the rule)
    """
    tutor = _tutor(client)
    sched = _schedule(client, tutor["id"], [
        {"day_of_week": 6, "start_time": "22:00:00", "end_time": "23:59:00"},  # Sun evening
        {"day_of_week": 0, "start_time": "00:00:00", "end_time": "02:00:00"},  # Mon early AM
    ])
    et = _event_type(client, _avail(tutor["id"], sched["id"]),
                     duration_minutes=60, recurring=True)

    # Midnight-crossing infinite series: Sun 23:00 → Mon 01:00
    series = _insert_series(db, tutor["id"], et["id"],
                            start_dow=6, start_t=time(23, 0),
                            end_dow=0, end_t=time(1, 0))
    _insert_booking(db, tutor["id"], et["id"], _dt(SUN, 23), _dt(MON, 1), series=series)

    # Sunday: only 22:00 slot survives; 23:00 is inside the rule's Sun half
    sun_slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], SUN, SUN)).json()
    assert _has_start(sun_slots, SUN, 22)
    assert not _has_start(sun_slots, SUN, 23)

    # Monday: only 01:00 slot survives; 00:00 is inside the rule's Mon half
    mon_slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()
    assert _has_start(mon_slots, MON, 1)
    assert not _has_start(mon_slots, MON, 0)


# ── Multi-tutor ───────────────────────────────────────────────────────────────

def test_multiple_tutors_busy_blocks_are_independent(client, db):
    """Two tutors on the same event type. Tutor A has a conflict at 09:00;
    tutor B does not. Each tutor's busy_dict is isolated — B's 09:00 slot survives."""
    tutor_a = _tutor(client)
    tutor_b = client.post("/tutors/", json={"first_name": "B", "last_name": "B", "pay_rate": 0}).json()

    sched_a = _mon_9_17(client, tutor_a["id"])
    sched_b = _schedule(client, tutor_b["id"],
                        [{"day_of_week": 0, "start_time": "09:00:00", "end_time": "17:00:00"}])

    et = _event_type(client, [
        {"tutor_id": tutor_a["id"], "schedule_id": sched_a["id"]},
        {"tutor_id": tutor_b["id"], "schedule_id": sched_b["id"]},
    ])

    _insert_booking(db, tutor_a["id"], et["id"], _dt(MON, 9), _dt(MON, 10, 30))

    slots = client.get("/available-slots/",
                       params=_params([tutor_a["id"], tutor_b["id"]], et["id"], MON, MON)).json()

    a_slots = [s for s in slots if s["tutor_id"] == tutor_a["id"]]
    b_slots = [s for s in slots if s["tutor_id"] == tutor_b["id"]]

    assert not _has_start(a_slots, MON, 9)   # blocked for A
    assert _has_start(b_slots, MON, 9)        # free for B
    assert len(b_slots) == 5                  # B has all 5 slots intact


# ── Midnight-crossing ─────────────────────────────────────────────────────────

def _sun_night_sched(client, tutor_id: int) -> dict:
    """Two adjacent rows: Sun 23:30–23:59 + Mon 00:00–00:30.
    resolve_schedule merges them into one block (SUN 23:30, MON 00:30)."""
    return _schedule(client, tutor_id, [
        {"day_of_week": 6, "start_time": "23:30:00", "end_time": "23:59:00"},
        {"day_of_week": 0, "start_time": "00:00:00", "end_time": "00:30:00"},
    ])


def test_standalone_free_block_spanning_midnight_produces_slot(client):
    """Schedule row Sun 23:30→Mon 00:30, 60-min duration.
    The single slot (23:30→00:30) must appear in a Sun–Mon query window."""
    tutor = _tutor(client)
    sched = _sun_night_sched(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), duration_minutes=60)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], SUN, MON)).json()

    assert _has_start(slots, SUN, 23, 30)


def test_midnight_crossing_busy_block_blocks_slot_on_far_side(client, db):
    """Booking spans Sun 23:00 → Mon 00:30. Schedule: Sun 22:00–23:59 + Mon 00:00–02:00.
    gather_busy_slice must pull Sunday's bucket when checking Monday blocks.
    Mon 00:00 slot overlaps the booking → blocked. Sun 22:00 and Mon 01:00 survive."""
    tutor = _tutor(client)
    sched = _schedule(client, tutor["id"], [
        {"day_of_week": 6, "start_time": "22:00:00", "end_time": "23:59:00"},
        {"day_of_week": 0, "start_time": "00:00:00", "end_time": "02:00:00"},
    ])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), duration_minutes=60)
    _insert_booking(db, tutor["id"], et["id"], _dt(SUN, 23), _dt(MON, 0, 30))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], SUN, MON)).json()

    assert _has_start(slots, SUN, 22)       # before the booking → free
    assert not _has_start(slots, MON, 0)    # overlaps booking → blocked
    assert _has_start(slots, MON, 1)        # after booking ends → free


def test_standalone_busy_before_time_min_spills_into_window(client, db):
    """Booking starts SUN 23:00 (before time_min=MON 00:00) and ends MON 00:30.
    gather_busy_slice pulls one extra day before the range start to catch overnight
    bookings bucketed on Sunday. Mon 00:00 slot must be blocked; Mon 01:00 survives."""
    tutor = _tutor(client)
    sched = _schedule(client, tutor["id"], [
        {"day_of_week": 6, "start_time": "22:00:00", "end_time": "23:59:00"},
        {"day_of_week": 0, "start_time": "00:00:00", "end_time": "02:00:00"},
    ])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), duration_minutes=60)
    _insert_booking(db, tutor["id"], et["id"], _dt(SUN, 23), _dt(MON, 0, 30))

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert not _has_start(slots, MON, 0)   # booking spills in from Sunday → blocked
    assert _has_start(slots, MON, 1)       # after booking ends → free


def test_infinite_free_block_spanning_midnight_produces_slot(client):
    """Infinite mode: same midnight-crossing schedule row, window starts on Sunday.
    resolve_to_first_occurrence resolves the dateless piece to (SUN 23:30, MON 00:30)."""
    tutor = _tutor(client)
    sched = _sun_night_sched(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), duration_minutes=60, recurring=True)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], SUN, MON)).json()

    assert _has_start(slots, SUN, 23, 30)


def test_infinite_free_block_spanning_midnight_time_min_on_monday(client):
    """Infinite mode, window starts on Monday (time_min=MON 00:00).
    Schedule: Sun 23:00–23:59 + Mon 00:00–02:00 → seam piece ((6,23:00),(0,02:00)).
    resolve_to_first_occurrence would normally go 6 days forward to SUN-1, putting
    the Monday tail at MON-2 outside the window. The one-week-back guard must detect
    that stepping back produces (SUN-0 23:00, MON-1 02:00) whose end >= time_min,
    and use that instead. Mon 00:00 and Mon 01:00 slots must appear."""
    tutor = _tutor(client)
    sched = _schedule(client, tutor["id"], [
        {"day_of_week": 6, "start_time": "23:00:00", "end_time": "23:59:00"},
        {"day_of_week": 0, "start_time": "00:00:00", "end_time": "02:00:00"},
    ])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), duration_minutes=60, recurring=True)

    slots = client.get("/available-slots/", params=_params(tutor["id"], et["id"], MON, MON)).json()

    assert _has_start(slots, MON, 0)   # Monday midnight tail of the seam block
    assert _has_start(slots, MON, 1)   # still within Mon 00:00–02:00


# ── DST / Timezone Safety ─────────────────────────────────────────────────────
#
# All tests here set Settings.business_timezone = "America/New_York" so the
# algorithm interprets schedule and series times in canonical NY timezone.
# The existing tests above leave Settings unseeded, causing the algorithm to
# fall back to "UTC" — so there is no cross-test interference.
#
# US 2025 transitions: spring forward March 9 (Sun 2am→3am), fall back Nov 2.
# Europe/Madrid 2025: spring forward March 30 — 3-week gap where US/SF are in
# EDT but Spain is still in CET. Since all bookings are stored in canonical NY
# time, this gap has no effect on adjacent-lesson continuity.

# NY calendar anchor dates
MON_BEFORE_SPRING = date(2025, 3, 3)    # Monday — NY is EST (UTC-5)
MON_AFTER_SPRING  = date(2025, 3, 10)   # Monday — NY is EDT (UTC-4)
MON_IN_US_ONLY_DST = date(2025, 3, 17)  # US in EDT, Spain still in CET
MON_BEFORE_FALL   = date(2025, 10, 27)  # Monday — NY is EDT (UTC-4)
MON_AFTER_FALL    = date(2025, 11, 3)   # Monday — NY is EST (UTC-5)


def _set_business_tz(db, tz: str = "America/New_York") -> None:
    s = db.query(Settings).filter(Settings.id == 1).first()
    if not s:
        s = Settings(id=1, business_timezone=tz)
        db.add(s)
    else:
        s.business_timezone = tz
    db.commit()


def _ny(d: date, h: int, m: int = 0) -> datetime:
    """UTC equivalent of h:mm NY local time on date d — respects EST vs EDT automatically."""
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(UTC)


def _dst_params(tutor_ids, event_type_id: int, d_min: date, d_max: date) -> dict:
    """Like _params but anchors at 6am NY time so time_min is safely on the right local day."""
    ids = [tutor_ids] if isinstance(tutor_ids, int) else list(tutor_ids)
    return {
        "tutor_ids": ids,
        "event_type_id": event_type_id,
        "time_min": _ny(d_min, 6).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_max": _ny(d_max, 20).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _mon_9_12_ny(client, tutor_id: int) -> dict:
    """Mon 09:00–12:00 schedule stored with NY canonical timezone."""
    return _schedule(client, tutor_id, [
        {"day_of_week": 0, "start_time": "09:00:00", "end_time": "12:00:00"},
    ], timezone="America/New_York")


def test_dst_canonical_busy_block_stable_across_spring_forward(client, db):
    """Two standalone bookings: one before spring forward (stored as 14:00 UTC = 9am EST),
    one after (13:00 UTC = 9am EDT). Both should block the 09:00 NY slot on their respective
    dates — proving busy_dict converts UTC → canonical NY correctly on both sides of DST."""
    _set_business_tz(db)
    tutor = _tutor(client)
    sched = _mon_9_12_ny(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))

    # 9am EST = 14:00 UTC; 9am EDT = 13:00 UTC — different UTC, same canonical NY time
    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_BEFORE_SPRING, 9), _ny(MON_BEFORE_SPRING, 10, 30))
    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_AFTER_SPRING, 9), _ny(MON_AFTER_SPRING, 10, 30))

    slots_before = client.get("/available-slots/",
                              params=_dst_params(tutor["id"], et["id"],
                                                 MON_BEFORE_SPRING, MON_BEFORE_SPRING)).json()
    assert not _has_start(slots_before, MON_BEFORE_SPRING, 9)    # blocked by EST booking
    assert _has_start(slots_before, MON_BEFORE_SPRING, 10, 30)   # 10:30 still free

    slots_after = client.get("/available-slots/",
                             params=_dst_params(tutor["id"], et["id"],
                                                MON_AFTER_SPRING, MON_AFTER_SPRING)).json()
    assert not _has_start(slots_after, MON_AFTER_SPRING, 9)      # blocked by EDT booking
    assert _has_start(slots_after, MON_AFTER_SPRING, 10, 30)     # 10:30 still free


def test_dst_adjacent_infinite_series_no_gap_across_spring_forward(client, db):
    """Two infinite series at adjacent canonical NY times (09:00–10:30 and 10:30–12:00)
    together cover the full Mon 09:00–12:00 NY schedule window. thin_schedule_dateless
    operates in (weekday, canonical-time) space, so the two rules leave ZERO surviving
    pieces regardless of whether the query date is before or after spring forward.

    This is the 'Arizona tutor, SF + Spain students' scenario: the SF student's booking
    is stored as 09:00 NY (was 06:00 SF in summer, 07:00 SF in winter — their local time
    shifts but the canonical slot doesn't). The Spain student's booking is stored as
    10:30 NY. Spain springs forward 3 weeks after the US (March 30 vs March 9 in 2025),
    creating a window where US/SF are in EDT but Spain is still in CET — but since both
    bookings are canonical NY, no phantom gap opens between adjacent lessons during that
    window."""
    _set_business_tz(db)
    tutor = _tutor(client)
    sched = _mon_9_12_ny(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]), recurring=True)

    # Series A: SF student booked Mon 09:00–10:30 NY (stored in canonical NY)
    series_a = _insert_series(db, tutor["id"], et["id"],
                              start_dow=0, start_t=time(9, 0),
                              end_dow=0, end_t=time(10, 30))
    # Series B: Spain student booked Mon 10:30–12:00 NY (different local DST dates, same canonical)
    series_b = _insert_series(db, tutor["id"], et["id"],
                              start_dow=0, start_t=time(10, 30),
                              end_dow=0, end_t=time(12, 0))

    # Seed one confirmed occurrence each so the series appear in busy_dict
    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_AFTER_SPRING, 9), _ny(MON_AFTER_SPRING, 10, 30), series=series_a)
    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_AFTER_SPRING, 10, 30), _ny(MON_AFTER_SPRING, 12), series=series_b)

    # The two series together cover 09:00–12:00 with no gap — thinned schedule is empty
    for query_date in [MON_BEFORE_SPRING, MON_AFTER_SPRING, MON_IN_US_ONLY_DST]:
        slots = client.get("/available-slots/",
                           params=_dst_params(tutor["id"], et["id"],
                                              query_date, query_date)).json()
        assert slots == [], (
            f"Expected no free slots on {query_date}: adjacent series should cover full "
            "Mon 09:00–12:00 NY window with no phantom gap"
        )


def test_dst_fall_back_adjacent_bookings_do_not_overlap(client, db):
    """At fall back (EDT→EST, Nov 2 2025) the risk is OVERLAP, not a gap: a booking
    at 10:30am EDT (14:30 UTC) could be incorrectly shifted to 9:30am NY if the
    UTC→canonical conversion used the wrong DST offset for that date. This would
    make two adjacent bookings appear to collapse into each other.

    Standalone mode — no thin_schedule_dateless — isolates the busy_dict conversion.

    Key assertion: inserting ONLY the 10:30am booking must leave 9am still free.
    If the booking shifted backward to 9:30am NY it would overlap the 9am slot
    and block it, failing the assertion."""
    _set_business_tz(db)
    tutor = _tutor(client)
    sched = _mon_9_12_ny(client, tutor["id"])
    et = _event_type(client, _avail(tutor["id"], sched["id"]))

    # --- before fall back (EDT) ---
    # Insert ONLY the 10:30am EDT booking first
    # 10:30am EDT = 14:30 UTC
    b2_before = _insert_booking(db, tutor["id"], et["id"],
                                _ny(MON_BEFORE_FALL, 10, 30), _ny(MON_BEFORE_FALL, 12))

    slots = client.get("/available-slots/",
                       params=_dst_params(tutor["id"], et["id"],
                                          MON_BEFORE_FALL, MON_BEFORE_FALL)).json()
    assert _has_start(slots, MON_BEFORE_FALL, 9)           # 9am still free — booking didn't shift into it
    assert not _has_start(slots, MON_BEFORE_FALL, 10, 30)  # 10:30am correctly blocked

    # Now add the 9am EDT booking — both slots must be blocked, still adjacent (not overlapping)
    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_BEFORE_FALL, 9), _ny(MON_BEFORE_FALL, 10, 30))

    slots = client.get("/available-slots/",
                       params=_dst_params(tutor["id"], et["id"],
                                          MON_BEFORE_FALL, MON_BEFORE_FALL)).json()
    assert not _has_start(slots, MON_BEFORE_FALL, 9)
    assert not _has_start(slots, MON_BEFORE_FALL, 10, 30)

    # --- after fall back (EST) ---
    # 10:30am EST = 15:30 UTC — different UTC value, same canonical NY time
    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_AFTER_FALL, 10, 30), _ny(MON_AFTER_FALL, 12))

    slots = client.get("/available-slots/",
                       params=_dst_params(tutor["id"], et["id"],
                                          MON_AFTER_FALL, MON_AFTER_FALL)).json()
    assert _has_start(slots, MON_AFTER_FALL, 9)            # 9am still free — no backward shift
    assert not _has_start(slots, MON_AFTER_FALL, 10, 30)   # 10:30am correctly blocked

    _insert_booking(db, tutor["id"], et["id"],
                    _ny(MON_AFTER_FALL, 9), _ny(MON_AFTER_FALL, 10, 30))

    slots = client.get("/available-slots/",
                       params=_dst_params(tutor["id"], et["id"],
                                          MON_AFTER_FALL, MON_AFTER_FALL)).json()
    assert not _has_start(slots, MON_AFTER_FALL, 9)
    assert not _has_start(slots, MON_AFTER_FALL, 10, 30)


def test_dst_series_occurrence_stays_at_canonical_time_across_transitions(client, db):
    """Standalone booking 9am–10:30am NY + a confirmed series occurrence 10:30am–12pm NY.
    Together they cover Mon 09:00–12:00 NY with no gap. Both appear in busy_dict as confirmed
    Booking rows — the algorithm does not distinguish standalone vs series at that stage.

    Query event type: standalone, 60-min duration, 30-min interval.
    30-min steps are required to expose both failure modes:

      Fall back squeeze: if busy_dict converts the 10:30am EDT occurrence incorrectly to
        9:30am NY (wrong offset applied), busy is [9–10:30] + [9:30–11] → 11am+60min=12pm
        is outside both intervals → spurious 11am slot appears.

      Spring forward gap: if busy_dict converts the 10:30am EST occurrence incorrectly to
        11:30am NY (wrong offset applied), busy is [9–10:30] + [11:30–1pm] → 10:30am
        candidate has end 11:30 which does NOT overlap [11:30,…) (strict comparison) →
        spurious 10:30am slot appears.

    Schedule 9am–12:30pm gives enough room for the tail/gap to become a 60-min candidate.
    Standalone query mode means thin_schedule_dateless never runs — only busy_dict
    UTC→canonical conversion is exercised."""
    _set_business_tz(db)
    tutor = _tutor(client)
    sched = _schedule(client, tutor["id"], [
        {"day_of_week": 0, "start_time": "09:00:00", "end_time": "12:30:00"},
    ], timezone="America/New_York")
    # 30-min interval so 10:30am is a generated candidate (default interval = duration = 60min
    # would only generate :00 slots, making the spring-forward gap undetectable)
    et = _event_type(client, _avail(tutor["id"], sched["id"]),
                     duration_minutes=60, interval_minutes=30)

    # Insert correct-UTC bookings for every test date upfront.
    # Each date's query window (6am–8pm NY) only sees its own day, so insertions don't interfere.
    test_dates = [MON_BEFORE_SPRING, MON_AFTER_SPRING, MON_BEFORE_FALL, MON_AFTER_FALL]
    for d in test_dates:
        _insert_booking(db, tutor["id"], et["id"], _ny(d, 9),     _ny(d, 10, 30))  # standalone
        _insert_booking(db, tutor["id"], et["id"], _ny(d, 10, 30), _ny(d, 12))     # series occ

    for d in test_dates:
        slots = client.get("/available-slots/",
                           params=_dst_params(tutor["id"], et["id"], d, d)).json()

        # Spring forward check: if occurrence shifted from 10:30am to 11:30am NY,
        # a 60-min gap [10:30–11:30] opens and the 10:30am candidate becomes free.
        assert not _has_start(slots, d, 10, 30), (
            f"{d}: spurious 10:30am slot — occurrence may have drifted forward "
            "(spring forward: 10:30am EST read as 11:30am EDT)"
        )
        # Fall back check: if occurrence shifted from 10:30am to 9:30am NY,
        # the busy intervals merge and expose an 11am tail.
        assert not _has_start(slots, d, 11), (
            f"{d}: spurious 11am slot — occurrence may have drifted backward "
            "(fall back: 10:30am EDT read as 9:30am EST)"
        )
        assert slots == [], (
            f"{d}: unexpected free slots — standalone [9–10:30] + occurrence [10:30–12] "
            "should cover the full window leaving no free 60-min candidate"
        )
