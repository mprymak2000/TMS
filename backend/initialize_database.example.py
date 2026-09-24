import requests
import psycopg2
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

API = "http://localhost:8000"
DB_URL = "postgresql://postgres:password@localhost:5432/tms"

# Template only — replace with your real data in a local, gitignored initialize_database.py.
# Identity and enrollment are separate rows: the name and email make a Contact, the rate and dates
# make the Enrollment that bills them.
students = [
    {"first_name": "Jane", "last_name": "Doe", "email": "jane.doe@example.com", "phone": "555-0100",
     "rate": 50, "rate_unit": "per_hour", "started_on": "2026-01-01"},
    {"first_name": "John", "last_name": "Smith", "email": "john.smith@example.com", "phone": "555-0101",
     "rate": 50, "rate_unit": "per_hour", "started_on": "2025-01-01"},
]

tutors = [
    {"first_name": "Your", "last_name": "Name", "pay_rate": 0, "is_active": True, "calendar_id": "your-email@example.com", "check_calendar_conflicts": True},
]

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute("""
    TRUNCATE TABLE
        invoice_lines, invoices,
        booking_requests, bookings, booking_series,
        lessons, booking_link_availability, schedule_days,
        schedules, booking_links, booking_types,
        contact_managers, enrollments, contacts, tutors
    RESTART IDENTITY CASCADE;
""")
conn.commit()
print("All tables cleared.")
cur.close()
conn.close()

# Settings (business_timezone) only gets created via GET /settings/'s get-or-create
# logic — nothing else creates this singleton row automatically.
requests.get(f"{API}/settings/")

for s in students:
    contact = requests.post(f"{API}/contacts/", json={
        "first_name": s["first_name"],
        "last_name": s["last_name"],
        "email": s["email"],
        "phone": s["phone"],
    }).json()
    # PUT, not POST: an enrollment's id is the contact's, so the URL names it before it exists.
    r = requests.put(f"{API}/contacts/{contact['id']}/enrollment", json={
        "rate": s["rate"],
        "rate_unit": s["rate_unit"],
        "started_on": s["started_on"],
    })
    print(r.status_code, s["first_name"], s["last_name"])

print("\nAll enrollments created\n")

for t in tutors:
    r = requests.post(f"{API}/tutors", json=t)
    print(r.status_code, r.json().get("first_name"), r.json().get("last_name"))

print("\nAll tutors created\n")

students_response = requests.get(f"{API}/contacts/?enrolled=true").json()["items"]
tutors_response = requests.get(f"{API}/tutors").json()

weekday_days = [
    {"day_of_week": i, "start_time": "10:30:00", "end_time": "22:00:00"}
    for i in range(5)  # Mon-Fri
]
weekend_days = [
    {"day_of_week": i, "start_time": "10:30:00", "end_time": "14:00:00"}
    for i in range(5, 7)  # Sat-Sun
]
schedule_days = weekday_days + weekend_days

schedule_ids = {}
for t in tutors_response:
    r = requests.post(f"{API}/schedules", json={
        "tutor_id": t["id"],
        "name": "Regular Hours",
        "is_default": True,
        "timezone": "America/New_York",
        "days": schedule_days,
    })
    schedule_ids[t["id"]] = r.json()["id"]
    print(r.status_code, f"Schedule for {t['first_name']}")

print("\nAll schedules created\n")

booking_link_recurring = requests.post(f"{API}/booking_links", json={
    "slug": "tutoring-session",
    "description": "Recurring — books a weekly repeating slot (same tutor, day, and time every week) rather than a single date. Cancellations and reschedules are approved automatically if requested at least 24 hours before the session; requests inside that 24-hour window are held for manual approval instead.",
    "duration_minutes": 90,
    "recurring": True,
    "price": 100,
    "price_unit": "per_session",
    "cancel_mode": "auto_window_request",
    "cancel_notice_minutes": 1440,
    "reschedule_mode": "auto_window_request",
    "reschedule_notice_minutes": 1440,
    "availability": [{"tutor_id": t["id"], "schedule_id": schedule_ids[t["id"]]} for t in tutors_response],
}).json()
print(f"Event type created: {booking_link_recurring['slug']} (id={booking_link_recurring['id']})")

booking_link_standalone = requests.post(f"{API}/booking_links", json={
    "slug": "one-time-lesson",
    "description": "Standalone — a single one-off session, not part of a recurring weekly series. Same 24-hour cancellation/reschedule policy as the recurring option: auto-approved outside the 24-hour window, held for manual approval if requested closer to the session.",
    "duration_minutes": 60,
    "recurring": False,
    "price": 80,
    "price_unit": "per_session",
    "cancel_mode": "auto_window_request",
    "cancel_notice_minutes": 1440,
    "reschedule_mode": "auto_window_request",
    "reschedule_notice_minutes": 1440,
    "availability": [{"tutor_id": t["id"], "schedule_id": schedule_ids[t["id"]]} for t in tutors_response],
}).json()
print(f"Event type created: {booking_link_standalone['slug']} (id={booking_link_standalone['id']})\n")

# ~3 months of fake historical lessons, hardcoded (not read from a spreadsheet — this is
# synthetic demo data either way, so generating it directly here skips a pointless
# Python -> xlsx -> Python round trip. The real initialize_database.py still imports
# actual historical lessons from a real xlsx, since that's a genuine one-time data
# migration need, not something worth mirroring in a demo template).
jane = next(c for c in students_response if c["first_name"] == "Jane")
john = next(c for c in students_response if c["first_name"] == "John")
# One id serves both: an enrollment's id IS the contact's, so it works for Lesson and Booking alike.
jane_id, john_id = jane["id"], john["id"]
tutor_id = tutors_response[0]["id"]

lesson_students = [
    {"enrollment_id": jane_id, "day_offset": 0},  # Mondays
    {"enrollment_id": john_id, "day_offset": 2},  # Wednesdays
]
notes_cycle = ["Great progress", "Review session", "Quiz prep", "Homework review", ""]
start = date(2026, 1, 5)  # first Monday of Jan 2026
weeks = 13  # ~3 months

lessons = []
for week in range(weeks):
    for s in lesson_students:
        lesson_date = start + timedelta(weeks=week, days=s["day_offset"])
        lessons.append({
            "date": str(lesson_date),
            "enrollment_id": s["enrollment_id"],
            "tutor_id": tutor_id,
            "hrs": 1.0,
            "pay_status": True,
            "notes": notes_cycle[len(lessons) % len(notes_cycle)],
        })

print(f"{len(lessons)} lessons generated. Submitting...")
response = requests.post(f"{API}/lessons/bulk_create", json=lessons)
if response.ok:
    print(f"Done. {len(response.json())} lessons created.")
else:
    print(f"Failed: {response.status_code} — {response.json()}")

# --- Demo bookings, inserted directly into the DB (bypassing the API) ---
#
# POST /bookings/ requires a real Google Calendar service account (it creates the
# calendar event first, then the DB row, atomically) — booking_id.google_event_id is
# NOT NULL by design (see CLAUDE.md: "a booking without a calendar event is a broken
# record"). There's no sensible way to fake a Google account for a public demo, so
# this seed script can't go through the real create_booking flow at all. Instead we
# insert Booking/BookingSeries rows directly with an obviously-fake google_event_id.
# These rows will LOOK like real bookings in the UI, but any reschedule/cancel action
# on them will fail — those endpoints all call get_calendar_service(), which needs a
# real GOOGLE_SERVICE_ACCOUNT_JSON in .env. That's expected in this demo environment,
# not a bug — see CLAUDE.md's Google Calendar Integration section for the real setup.
#
# TRAP when editing the INSERTs below: several NOT NULL columns get their value from a
# PYTHON-side default (Column(..., default=...)), which SQLAlchemy applies on ORM writes but
# raw SQL bypasses completely. Miss one and this script dies on a NOT NULL violation — there's
# no static check that catches it. As of now that set is:
#   bookings        public_id, timezone, status, is_no_show, sms_opt_in
#   booking_series  public_id, freq, interval, sms_opt_in
# Add a NOT NULL column with a Python-side default to either model and it must be added here too.
# To list the current set: for each NOT NULL, non-PK column, flag any with .default set and
# .server_default None. TODO: server_default would make these safe for raw SQL and delete this
# whole footgun — needs Alembic first (see the tms-roadmap skill).
tz = ZoneInfo("America/New_York")


def next_weekday(target_weekday: int) -> date:
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    days_ahead = days_ahead or 7  # always the *next* occurrence, not today
    return today + timedelta(days=days_ahead)


def to_utc(local_date: date, local_time: str) -> datetime:
    hour, minute = map(int, local_time.split(":"))
    return datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz).astimezone(ZoneInfo("UTC"))


conn = psycopg2.connect(DB_URL)
cur = conn.cursor()


def insert_standalone_booking(event_date, start_time, end_time, booking_link_id, contact_id, google_event_id):
    """payer and attendee are the same contact here — these demo clients book for themselves."""
    cur.execute("""
        INSERT INTO bookings (
            public_id, tutor_id, booking_link_id, start, "end", timezone, google_event_id, status,
            is_no_show, sms_opt_in, payer_id, attendee_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed', false, false, %s, %s)
    """, (
        str(uuid4()), tutor_id, booking_link_id,
        to_utc(event_date, start_time), to_utc(event_date, end_time), "America/New_York",
        google_event_id, contact_id, contact_id,
    ))


# A few ordinary standalone bookings, spread across different days/times.
insert_standalone_booking(
    next_weekday(1), "16:00", "17:00",  # next Tuesday
    booking_link_standalone["id"], jane_contact,
    "demo-standalone-fake-event-id-1",
)
insert_standalone_booking(
    next_weekday(4), "11:00", "12:00",  # next Friday
    booking_link_standalone["id"], jane_contact,
    "demo-standalone-fake-event-id-2",
)
insert_standalone_booking(
    next_weekday(4), "15:00", "16:00",  # next Friday
    booking_link_standalone["id"], john_contact,
    "demo-standalone-fake-event-id-3",
)
insert_standalone_booking(
    date.today() - timedelta(days=14), "13:00", "14:00",  # two weeks ago — exercises the "Past" view
    booking_link_standalone["id"], jane_contact,
    "demo-standalone-fake-event-id-4",
)

# Fully books the tutor's entire schedule window on this day (10:30-22:00, matching the
# weekday_days schedule above) — nothing else can fit, so GET /bookings/available-slots
# for this date should return zero free slots. Demonstrates the busy-block exclusion
# logic actually working, not just an empty/never-tested code path.
conflict_date = next_weekday(3)  # next Thursday
insert_standalone_booking(
    conflict_date, "10:30", "22:00",
    booking_link_standalone["id"], john_contact,
    "demo-fully-booked-day-fake-event-id",
)

# Recurring series: John, Tutoring Session, weekly Wednesdays 5:00-6:30pm, 8 occurrences.
series_start = next_weekday(2)  # Wednesday
series_dtstart = datetime.combine(series_start, time(17, 0))
series_dtend = datetime.combine(series_start, time(18, 30))
series_until = series_start + timedelta(weeks=7)
series_public_id = str(uuid4())
cur.execute("""
    INSERT INTO booking_series (
        public_id, tutor_id, booking_link_id, dtstart, dtend, freq, interval, until, google_event_id,
        sms_opt_in, payer_id, attendee_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s, %s)
    RETURNING id
""", (
    series_public_id, tutor_id, booking_link_recurring["id"], series_dtstart, series_dtend,
    "WEEKLY", 1, series_until,
    "demo-series-fake-event-id",
    john_contact, john_contact,
))
series_id = cur.fetchone()[0]

# A bounded series has EVERY occurrence materialized at creation — only indefinite series are kept
# sparse for the extender to fill in. Occurrence public_ids use the composite
# "{series.public_id}:{unix_timestamp}" form that _ensure_occurrence writes in production.
for week in range(8):
    occ_date = series_start + timedelta(weeks=week)
    occ_start = to_utc(occ_date, "17:00")
    cur.execute("""
        INSERT INTO bookings (
            public_id, series_id, tutor_id, booking_link_id, start, "end", timezone, google_event_id, status,
            is_no_show, sms_opt_in, payer_id, attendee_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', false, false, %s, %s)
    """, (
        f"{series_public_id}:{int(occ_start.timestamp())}",
        series_id, tutor_id, booking_link_recurring["id"],
        occ_start, to_utc(occ_date, "18:30"), "America/New_York",
        "demo-series-fake-event-id",
        john_contact, john_contact,
    ))

conn.commit()
cur.close()
conn.close()
print("\n5 standalone bookings (one past, one fully blocking a day) + 1 recurring series (8 occurrences) created.")
print("Note: these have fake google_event_id values — reschedule/cancel on them will")
print("fail without a real GOOGLE_SERVICE_ACCOUNT_JSON configured in .env.")
