import requests
import psycopg2
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

API = "http://localhost:8000"
DB_URL = "postgresql://postgres:password@localhost:5432/tms"

# Template only — replace with your real data in a local, gitignored initialize_database.py.
students = [
    {"first_name": "Jane", "last_name": "Doe", "rate": 50, "start_date": "2026-01-01", "is_active": True},
    {"first_name": "John", "last_name": "Smith", "rate": 50, "start_date": "2025-01-01", "is_active": True},
]

tutors = [
    {"first_name": "Your", "last_name": "Name", "pay_rate": 0, "is_active": True, "calendar_id": "your-email@example.com", "check_calendar_conflicts": True},
]

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute("""
    TRUNCATE TABLE
        booking_requests, bookings, booking_series,
        lessons, event_type_availability, schedule_days,
        schedules, event_types, students, tutors
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
    r = requests.post(f"{API}/students", json=s)
    print(r.status_code, r.json().get("first_name"), r.json().get("last_name"))

print("\nAll students created\n")

for t in tutors:
    r = requests.post(f"{API}/tutors", json=t)
    print(r.status_code, r.json().get("first_name"), r.json().get("last_name"))

print("\nAll tutors created\n")

students_response = requests.get(f"{API}/students").json()
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

event_type_recurring = requests.post(f"{API}/event_types", json={
    "name": "Tutoring Session",
    "description": "Recurring — books a weekly repeating slot (same tutor, day, and time every week) rather than a single date. Cancellations and reschedules are approved automatically if requested at least 24 hours before the session; requests inside that 24-hour window are held for manual approval instead.",
    "duration_minutes": 90,
    "recurring": True,
    "cancel_mode": "auto_window_request",
    "cancel_notice_minutes": 1440,
    "reschedule_mode": "auto_window_request",
    "reschedule_notice_minutes": 1440,
    "availability": [{"tutor_id": t["id"], "schedule_id": schedule_ids[t["id"]]} for t in tutors_response],
}).json()
print(f"Event type created: {event_type_recurring['name']} (id={event_type_recurring['id']})")

event_type_standalone = requests.post(f"{API}/event_types", json={
    "name": "One-time Lesson",
    "description": "Standalone — a single one-off session, not part of a recurring weekly series. Same 24-hour cancellation/reschedule policy as the recurring option: auto-approved outside the 24-hour window, held for manual approval if requested closer to the session.",
    "duration_minutes": 60,
    "recurring": False,
    "cancel_mode": "auto_window_request",
    "cancel_notice_minutes": 1440,
    "reschedule_mode": "auto_window_request",
    "reschedule_notice_minutes": 1440,
    "availability": [{"tutor_id": t["id"], "schedule_id": schedule_ids[t["id"]]} for t in tutors_response],
}).json()
print(f"Event type created: {event_type_standalone['name']} (id={event_type_standalone['id']})\n")

# ~3 months of fake historical lessons, hardcoded (not read from a spreadsheet — this is
# synthetic demo data either way, so generating it directly here skips a pointless
# Python -> xlsx -> Python round trip. The real initialize_database.py still imports
# actual historical lessons from a real xlsx, since that's a genuine one-time data
# migration need, not something worth mirroring in a demo template).
jane_id = next(s["id"] for s in students_response if s["first_name"] == "Jane")
john_id = next(s["id"] for s in students_response if s["first_name"] == "John")
tutor_id = tutors_response[0]["id"]

lesson_students = [
    {"student_id": jane_id, "day_offset": 0},  # Mondays
    {"student_id": john_id, "day_offset": 2},  # Wednesdays
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
            "student_id": s["student_id"],
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


def insert_standalone_booking(event_date, start_time, end_time, event_type_id, student_id, first, last, email, phone, google_event_id):
    cur.execute("""
        INSERT INTO bookings (
            tutor_id, event_type_id, start, "end", timezone, google_event_id, status,
            is_no_show, student_id, student_first, student_last, student_email, student_phone
        ) VALUES (%s, %s, %s, %s, %s, %s, 'confirmed', false, %s, %s, %s, %s, %s)
    """, (
        tutor_id, event_type_id,
        to_utc(event_date, start_time), to_utc(event_date, end_time), "America/New_York",
        google_event_id, student_id, first, last, email, phone,
    ))


# A few ordinary standalone bookings, spread across different days/times.
insert_standalone_booking(
    next_weekday(1), "16:00", "17:00",  # next Tuesday
    event_type_standalone["id"], jane_id, "Jane", "Doe", "jane.doe@example.com", "555-0100",
    "demo-standalone-fake-event-id-1",
)
insert_standalone_booking(
    next_weekday(4), "11:00", "12:00",  # next Friday
    event_type_standalone["id"], jane_id, "Jane", "Doe", "jane.doe@example.com", "555-0100",
    "demo-standalone-fake-event-id-2",
)
insert_standalone_booking(
    next_weekday(4), "15:00", "16:00",  # next Friday
    event_type_standalone["id"], john_id, "John", "Smith", "john.smith@example.com", "555-0101",
    "demo-standalone-fake-event-id-3",
)
insert_standalone_booking(
    date.today() - timedelta(days=14), "13:00", "14:00",  # two weeks ago — exercises the "Past" view
    event_type_standalone["id"], jane_id, "Jane", "Doe", "jane.doe@example.com", "555-0100",
    "demo-standalone-fake-event-id-4",
)

# Fully books the tutor's entire schedule window on this day (10:30-22:00, matching the
# weekday_days schedule above) — nothing else can fit, so GET /bookings/available-slots
# for this date should return zero free slots. Demonstrates the busy-block exclusion
# logic actually working, not just an empty/never-tested code path.
conflict_date = next_weekday(3)  # next Thursday
insert_standalone_booking(
    conflict_date, "10:30", "22:00",
    event_type_standalone["id"], john_id, "John", "Smith", "john.smith@example.com", "555-0101",
    "demo-fully-booked-day-fake-event-id",
)

# Recurring series: John, Tutoring Session, weekly Wednesdays 5:00-6:30pm, 8 occurrences.
series_start = next_weekday(2)  # Wednesday
recur_until = series_start + timedelta(weeks=7)
cur.execute("""
    INSERT INTO booking_series (
        tutor_id, event_type_id, start_day_of_week, end_day_of_week, start_time, end_time,
        timezone, is_active, recur_until, google_event_id,
        student_id, student_first, student_last, student_email, student_phone
    ) VALUES (%s, %s, 2, 2, '17:00', '18:30', %s, true, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
""", (
    tutor_id, event_type_recurring["id"], "America/New_York", recur_until,
    "demo-series-fake-event-id",
    john_id, "John", "Smith", "john.smith@example.com", "555-0101",
))
series_id = cur.fetchone()[0]

for week in range(8):
    occ_date = series_start + timedelta(weeks=week)
    cur.execute("""
        INSERT INTO bookings (
            series_id, tutor_id, event_type_id, start, "end", timezone, google_event_id, status,
            is_no_show, student_id, student_first, student_last, student_email, student_phone
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed', false, %s, %s, %s, %s, %s)
    """, (
        series_id, tutor_id, event_type_recurring["id"],
        to_utc(occ_date, "17:00"), to_utc(occ_date, "18:30"), "America/New_York",
        "demo-series-fake-event-id",
        john_id, "John", "Smith", "john.smith@example.com", "555-0101",
    ))

conn.commit()
cur.close()
conn.close()
print("\n5 standalone bookings (one past, one fully blocking a day) + 1 recurring series (8 occurrences) created.")
print("Note: these have fake google_event_id values — reschedule/cancel on them will")
print("fail without a real GOOGLE_SERVICE_ACCOUNT_JSON configured in .env.")
