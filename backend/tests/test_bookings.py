from datetime import datetime, timedelta, UTC
from unittest.mock import patch, MagicMock
from conftest import TestingSessionLocal
from models import Booking
from schemas import BookingResponse


def _all_bookings():
    """Raw DB truth (all rows, any status) — the old GET /bookings/ endpoint existed
    only for this, and tests are the only remaining consumer now that it's gone."""
    with TestingSessionLocal() as db:
        rows = db.query(Booking).order_by(Booking.id).all()
        return [BookingResponse.model_validate(b).model_dump(mode="json") for b in rows]


tutor_payload = {"first_name": "Tutor", "last_name": "Test", "pay_rate": 0, "calendar_id": "tutor@calendar.com"}

# Standalone — each POST creates exactly 1 Booking row
event_type_standalone = {
    "name": "One-off Session",
    "duration_minutes": 90,
    "recurring": False,
}

# Recurring indefinite (Mode C) — each POST creates 27 rows (week 0..26)
event_type_recurring = {
    "name": "Tutoring Session",
    "duration_minutes": 90,
    "recurring": True,
}

event_type_strict_notice = {
    "name": "Strict Notice Session",
    "duration_minutes": 90,
    "recurring": False,
    # 50M-minute notice: booking in 2099 (~38.4M min away) is always inside the window,
    # so token endpoints queue a request instead of executing immediately
    "cancel_mode": "auto_window_request",
    "cancel_notice_minutes": 50000000,
    "reschedule_mode": "auto_window_request",
    "reschedule_notice_minutes": 50000000,
}

event_type_recurring_strict_notice = {
    **event_type_strict_notice,
    "name": "Recurring Strict Notice Session",
    "recurring": True,
}

booking_payload = {
    "tutor_id": None,
    "event_type_id": None,
    "start": "2099-06-10T16:00:00",
    "end": "2099-06-10T17:30:00",
    "timezone": "America/New_York",
    "student_first": "Test",
    "student_last": "Smith",
    "student_email": "alex@example.com",
    "student_phone": "555-1234",
}

reschedule_payload = {
    "tutor_id": None,
    "start": "2099-08-10T16:00:00",
    "end": "2099-08-10T17:30:00",
    "timezone": "America/New_York",
}

MOCK_EVENT_ID = "google_event_abc123"
MOCK_INSTANCE_ID = "google_event_abc123_instance"


def mock_calendar_service():
    svc = MagicMock()
    svc.events().insert().execute.return_value = {"id": MOCK_EVENT_ID}
    svc.events().delete().execute.return_value = {}
    svc.events().patch().execute.return_value = {"id": MOCK_EVENT_ID}
    svc.events().get().execute.return_value = {"id": MOCK_EVENT_ID, "summary": "old"}
    svc.events().instances().execute.return_value = {"items": [{"id": MOCK_INSTANCE_ID, "start": {"dateTime": "2099-06-10T20:00:00Z"}, "end": {"dateTime": "2099-06-10T21:30:00Z"}}]}
    return svc


_schedule = {
    "name": "Default",
    "is_default": True,
    "timezone": "America/New_York",
    "days": [{"day_of_week": 0, "start_time": "09:00:00", "end_time": "17:00:00"}],
}


def make_tutor_with_schedule(client):
    tutor = client.post("/tutors/", json=tutor_payload).json()
    schedule = client.post("/schedules/", json={**_schedule, "tutor_id": tutor["id"]}).json()
    return tutor, [{"tutor_id": tutor["id"], "schedule_id": schedule["id"]}]


def setup_standalone(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_standalone, "availability": availability}).json()
    return tutor, event_type


def setup_recurring(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_recurring, "availability": availability}).json()
    return tutor, event_type


def setup_strict_notice(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_strict_notice, "availability": availability}).json()
    return tutor, event_type


def setup_recurring_strict_notice(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_recurring_strict_notice, "availability": availability}).json()
    return tutor, event_type


# ── CREATE ──────────────────────────────────────────────────────────────────

def test_create_standalone_booking(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post("/bookings/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["student_first"] == "Test"
    assert data["tutor_id"] == tutor["id"]
    assert data["google_event_id"] == MOCK_EVENT_ID
    assert data["series_id"] is None
    assert data["status"] == "confirmed"
    assert data["request"] is None


def test_create_recurring_booking_creates_series(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post("/bookings/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["series_id"] is not None
    assert data["status"] == "confirmed"
    # Mode C indefinite: 1 row created upfront; chained trigger generates the rest
    assert len(_all_bookings()) == 1


def test_create_booking_mode_a_fixed_expiry(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={
        "name": "Summer Class",
        "duration_minutes": 60,
        "recurring": True,
        "expires_on": "2099-09-01",
        "availability": availability,
    }).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post("/bookings/", json=payload)
    assert response.status_code == 201
    assert response.json()["series_id"] is not None
    all_bookings = _all_bookings()
    assert all(b["start"][:10] <= "2099-09-01" for b in all_bookings)
    assert len(all_bookings) == 12  # Jun 10 .. Aug 26 (12 Tuesdays ≤ Sep 1)


def test_create_booking_mode_b_locked(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={
        "name": "8-Week Course",
        "duration_minutes": 60,
        "recurring": True,
        "recur_weeks": 8,
        "booker_can_set_recur_until": False,
        "availability": availability,
    }).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post("/bookings/", json=payload)
    assert response.status_code == 201
    assert len(_all_bookings()) == 8  # weeks 0..7


def test_create_booking_mode_b_booker_set(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={
        "name": "Flexible Course",
        "duration_minutes": 60,
        "recurring": True,
        "recur_weeks": 8,
        "booker_can_set_recur_until": True,
        "availability": availability,
    }).json()
    payload = {
        **booking_payload,
        "tutor_id": tutor["id"],
        "event_type_id": event_type["id"],
        "recur_until": "2099-07-08",  # Jun 10 + 4 weeks = Jul 8
    }
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post("/bookings/", json=payload)
    assert response.status_code == 201
    assert len(_all_bookings()) == 5  # Jun 10, 17, 24, Jul 1, 8


def test_create_booking_tutor_not_found(client):
    _, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": 9999, "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 404


def test_create_booking_event_type_not_found(client):
    tutor, _ = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": 9999}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 404


def test_create_booking_tutor_no_calendar(client):
    tutor = client.post("/tutors/", json={"first_name": "No", "last_name": "Cal", "pay_rate": 0}).json()
    _, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_standalone, "name": "No Cal ET", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 400


def test_create_booking_no_email(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"],
               "student_email": None, "parent_email": None}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 422


def test_create_booking_no_phone(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"],
               "student_phone": None, "parent_phone": None}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 422


def test_create_booking_end_before_start(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"],
               "start": "2099-06-10T17:30:00", "end": "2099-06-10T16:00:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 422


def test_create_booking_in_past(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"],
               "start": "2020-01-01T10:00:00", "end": "2020-01-01T11:30:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/", json=payload).status_code == 422


def test_create_booking_calendar_failure_rolls_back(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    svc = mock_calendar_service()
    svc.events().insert().execute.side_effect = Exception("Google API down")
    with patch("routers.bookings.get_calendar_service", return_value=svc):
        assert client.post("/bookings/", json=payload).status_code == 500
    assert _all_bookings() == []


# ── GET ──────────────────────────────────────────────────────────────────────

def test_get_booking_by_id(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    response = client.get(f"/bookings/{created['id']}")
    assert response.status_code == 200
    assert response.json()["student_first"] == "Test"


def test_get_booking_not_found(client):
    assert client.get("/bookings/9999").status_code == 404


def test_get_bookings_pending_only(client):
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")
    assert len(_all_bookings()) == 1  # booking still exists (not executed)
    pending = client.get("/bookings/my-bookings?pending_only=true").json()["items"]
    assert len(pending) == 1
    assert pending[0]["request"]["type"] == "cancel_occurrence"
    assert pending[0]["request"]["status"] == "pending"


# ── CANCEL (soft-delete) ──────────────────────────────────────────────────────

def test_cancel_booking(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.delete(f"/bookings/{created['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(f"/bookings/{created['id']}").json()["status"] == "cancelled"


def test_cancel_booking_blocked_not_allowed_policy(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={"name": "No Cancel ET", "duration_minutes": 60, "recurring": False, "cancel_mode": "not_allowed", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        assert client.delete(f"/bookings/{created['id']}").status_code == 400


def test_cancel_booking_not_found(client):
    assert client.delete("/bookings/9999").status_code == 404


def test_cancel_already_cancelled(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.delete(f"/bookings/{created['id']}")
        assert client.delete(f"/bookings/{created['id']}").status_code == 400


# ── PERMANENT DELETE ──────────────────────────────────────────────────────────

def test_permanent_delete(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.delete(f"/bookings/{created['id']}/permanent")
    assert response.status_code == 204
    assert client.get(f"/bookings/{created['id']}").status_code == 404


def test_permanent_delete_cascade(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        original = client.post("/bookings/", json=payload).json()
        rescheduled = client.post(f"/bookings/{original['id']}/reschedule", json={**reschedule_payload, "tutor_id": tutor["id"]}).json()
        assert client.delete(f"/bookings/{rescheduled['id']}/permanent").status_code == 409
        assert client.delete(f"/bookings/{rescheduled['id']}/permanent?cascade=true").status_code == 204
    assert client.get(f"/bookings/{rescheduled['id']}").status_code == 404
    assert client.get(f"/bookings/{original['id']}").status_code == 404


# ── UPDATE (contact + no-show) ────────────────────────────────────────────────

def test_update_contact(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    response = client.put(f"/bookings/{created['id']}", json={
        "student_first": "Test",
        "student_last": "Smith",
        "student_email": "new@example.com",
        "student_phone": "555-9999",
    })
    assert response.status_code == 200
    assert response.json()["student_email"] == "new@example.com"


def test_mark_no_show(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    response = client.put(f"/bookings/{created['id']}", json={
        "student_first": created["student_first"],
        "student_last": created["student_last"],
        "student_email": created["student_email"],
        "student_phone": created["student_phone"],
        "is_no_show": True,
    })
    assert response.status_code == 200
    assert response.json()["is_no_show"] is True


# ── RESCHEDULE ────────────────────────────────────────────────────────────────

def test_reschedule_standalone(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        original = client.post("/bookings/", json=payload).json()
        response = client.post(f"/bookings/{original['id']}/reschedule", json={**reschedule_payload, "tutor_id": tutor["id"]})
    assert response.status_code == 200
    new_booking = response.json()
    assert new_booking["status"] == "confirmed"
    assert new_booking["series_id"] is None
    original_updated = client.get(f"/bookings/{original['id']}").json()
    assert original_updated["status"] == "rescheduled"
    assert original_updated["rescheduled_to"] == new_booking["id"]


def test_reschedule_not_found(client):
    tutor = client.post("/tutors/", json=tutor_payload).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/9999/reschedule", json={**reschedule_payload, "tutor_id": tutor["id"]}).status_code == 404


def test_reschedule_not_confirmed(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        original = client.post("/bookings/", json=payload).json()
        client.delete(f"/bookings/{original['id']}")
        assert client.post(f"/bookings/{original['id']}/reschedule", json={**reschedule_payload, "tutor_id": tutor["id"]}).status_code == 400


def test_reschedule_not_allowed_policy(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={"name": "No Reschedule ET", "duration_minutes": 60, "recurring": False, "reschedule_mode": "not_allowed", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        original = client.post("/bookings/", json=payload).json()
        assert client.post(f"/bookings/{original['id']}/reschedule", json={**reschedule_payload, "tutor_id": tutor["id"]}).status_code == 400


# ── SERIES CANCEL ─────────────────────────────────────────────────────────────

def test_cancel_series(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first = client.post("/bookings/", json=payload).json()
        series_id = first["series_id"]
        response = client.delete(f"/bookings/booking-series/{series_id}")
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    # all 2099 occurrences are future → bulk-deleted
    assert len(_all_bookings()) == 0


def test_cancel_series_not_found(client):
    assert client.delete("/bookings/booking-series/9999").status_code == 404


# ── MANAGE OCCURRENCE ─────────────────────────────────────────────────────────

def test_manage_occurrence_get(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    response = client.get(f"/bookings/manage-occurrence/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_manage_occurrence_get_not_found(client):
    assert client.get("/bookings/manage-occurrence/bad-token").status_code == 404


def test_manage_occurrence_cancel_direct(client):
    """min_notice=0 + far-future booking → executes cancel immediately."""
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_manage_occurrence_cancel_not_allowed_policy(client):
    """cancel_mode=not_allowed → 400."""
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={"name": "No Cancel Token ET", "duration_minutes": 60, "recurring": False, "cancel_mode": "not_allowed", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        assert client.post(f"/bookings/manage-occurrence/{created['id']}/cancel").status_code == 400


def test_manage_occurrence_cancel_creates_request(client):
    """min_notice > time until booking → queues cancel_occurrence request, booking stays confirmed."""
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["request"]["type"] == "cancel_occurrence"
    assert data["request"]["status"] == "pending"


def test_manage_occurrence_reschedule_direct(client):
    """min_notice=0 → executes reschedule immediately, returns new booking."""
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(
            f"/bookings/manage-occurrence/{created['id']}/reschedule",
            json={**reschedule_payload, "tutor_id": tutor["id"]},
        )
    assert response.status_code == 200
    new_booking = response.json()
    assert new_booking["status"] == "confirmed"
    assert new_booking["id"] != created["id"]
    assert client.get(f"/bookings/{created['id']}").json()["status"] == "rescheduled"


def test_manage_occurrence_reschedule_creates_request(client):
    """min_notice > time until booking → queues reschedule_occurrence request."""
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(
            f"/bookings/manage-occurrence/{created['id']}/reschedule",
            json={**reschedule_payload, "tutor_id": tutor["id"]},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["request"]["type"] == "reschedule_occurrence"
    assert data["request"]["status"] == "pending"
    assert data["request"]["requested_tutor_id"] == tutor["id"]


# ── MY-BOOKINGS ──────────────────────────────────────────────────────────────

def test_my_bookings_upcoming_merges_real_and_virtual(client):
    """Regression: real rows (Booking ORM objects) and virtual occurrences (BookingResponse
    instances) sorted together must not crash on naive-vs-aware datetime comparison."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    response = client.get(f"/bookings/my-bookings?email={created['student_email']}&page=1")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 2  # occurrence 1 (real) + at least one virtual occurrence
    assert items[0]["id"] == created["id"]  # sorted chronologically, real occurrence 1 comes first
    starts = [i["start"] for i in items]
    assert starts == sorted(starts)


def test_my_bookings_upcoming_excludes_other_customers(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    # different time to avoid the same-tutor overlap conflict check, not just a different student
    other_payload = {**payload, "student_email": "someone-else@example.com", "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json=payload)
        client.post("/bookings/", json=other_payload)
    items = client.get("/bookings/my-bookings?email=someone-else@example.com").json()["items"]
    assert len(items) == 1
    assert items[0]["student_email"] == "someone-else@example.com"


def test_my_bookings_no_email_shows_everyone(client):
    """The admin/no-filter case: omitting email entirely merges everyone's real + virtual
    occurrences — an admin shouldn't see a degraded, materialized-only view."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    other_payload = {**payload, "student_email": "someone-else@example.com", "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json=payload)
        client.post("/bookings/", json=other_payload)
    items = client.get("/bookings/my-bookings").json()["items"]
    emails = {i["student_email"] for i in items}
    assert "alex@example.com" in emails
    assert "someone-else@example.com" in emails
    assert any(":" in i["id"] for i in items)  # includes virtual occurrences, not just real rows


def test_my_bookings_tutor_id_filters_scope(client):
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_standalone, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_standalone, "name": "One-off Session B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"})
    items = client.get(f"/bookings/my-bookings?tutor_id={tutor_a['id']}").json()["items"]
    assert len(items) == 1
    assert items[0]["tutor_id"] == tutor_a["id"]


def test_my_bookings_past(client):
    """BookingCreate rejects past-dated starts outright, so a past booking has to be
    created normally and then backdated directly in the DB — the API itself can't produce one."""
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    db = TestingSessionLocal()
    booking = db.query(Booking).filter(Booking.public_id == created["id"]).first()
    booking.start = datetime(2020, 1, 7, 16, 0, tzinfo=UTC)
    booking.end = datetime(2020, 1, 7, 17, 0, tzinfo=UTC)
    db.commit()
    db.close()

    upcoming = client.get(f"/bookings/my-bookings?email={created['student_email']}&status=upcoming").json()["items"]
    past = client.get(f"/bookings/my-bookings?email={created['student_email']}&status=past").json()["items"]
    assert created["id"] not in [i["id"] for i in upcoming]
    assert created["id"] in [i["id"] for i in past]


def test_my_bookings_never_writes_to_db(client):
    """Browsing a page deep enough to require many virtual occurrences must not materialize any of them."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.get(f"/bookings/my-bookings?email={created['student_email']}&page=3")
    all_bookings = _all_bookings()
    assert len(all_bookings) == 1  # only the real occurrence 1 — nothing materialized by browsing


# ── SERVER-COMPUTED POLICY VERDICTS ─────────────────────────────────────────────

def test_booking_response_exposes_auto_policy(client):
    """No cancel_mode/reschedule_mode set on the event type → both actions are 'auto'."""
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    assert created["cancel_action"] == "auto"
    assert created["reschedule_action"] == "auto"


def test_booking_response_exposes_blocked_policy(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={
        "name": "No Cancel Or Reschedule ET", "duration_minutes": 60, "recurring": False,
        "cancel_mode": "not_allowed", "reschedule_mode": "not_allowed", "availability": availability,
    }).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    assert created["cancel_action"] == "blocked"
    assert created["reschedule_action"] == "blocked"


def test_booking_response_exposes_request_policy(client):
    """Inside the strict notice window → 'request'."""
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    assert created["cancel_action"] == "request"
    assert created["reschedule_action"] == "request"


def test_booking_series_response_exposes_policy_from_next_upcoming(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    series = client.get(f"/bookings/manage-series/{created['series_id']}").json()
    assert series["cancel_action"] == "auto"
    assert series["reschedule_action"] == "auto"


# ── MANAGE SERIES ──────────────────────────────────────────────────────────────

def test_manage_series_get(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
    response = client.get(f"/bookings/manage-series/{created['series_id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["series_id"]


def test_manage_series_get_not_found(client):
    assert client.get("/bookings/manage-series/bad-ref").status_code == 404


def test_manage_series_cancel_direct(client):
    """min_notice=0 + far-future booking → executes cancel immediately."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(f"/bookings/manage-series/{created['series_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_manage_series_cancel_not_allowed_policy(client):
    """cancel_mode=not_allowed → 400."""
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={"name": "No Cancel Series ET", "duration_minutes": 60, "recurring": True, "cancel_mode": "not_allowed", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        assert client.post(f"/bookings/manage-series/{created['series_id']}/cancel").status_code == 400


def test_manage_series_cancel_creates_request(client):
    """min_notice > time until booking → queues cancel_series request, series stays active."""
    tutor, event_type = setup_recurring_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(f"/bookings/manage-series/{created['series_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["is_active"] is True


def test_manage_series_reschedule_direct(client):
    """min_notice=0 → executes reschedule immediately."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(
            f"/bookings/manage-series/{created['series_id']}/reschedule",
            json={**reschedule_payload, "tutor_id": tutor["id"]},
        )
    assert response.status_code == 200
    assert response.json()["id"] == created["series_id"]
    all_bookings = _all_bookings()
    assert any(b["start"].startswith("2099-08-10") for b in all_bookings)


def test_manage_series_reschedule_creates_request(client):
    """min_notice > time until booking → queues reschedule_series request, series untouched."""
    tutor, event_type = setup_recurring_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        response = client.post(
            f"/bookings/manage-series/{created['series_id']}/reschedule",
            json={**reschedule_payload, "tutor_id": tutor["id"]},
        )
    assert response.status_code == 200
    all_bookings = _all_bookings()
    assert not any(b["start"].startswith("2099-08-10") for b in all_bookings)
    assert any(b["start"].startswith("2099-06-10") for b in all_bookings)  # original untouched


# ── APPROVE / DENY ────────────────────────────────────────────────────────────

def test_approve_cancel_request(client):
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")
        request_id = client.get(f"/bookings/{created['id']}").json()["request"]["id"]
        response = client.post(f"/bookings/booking-request/{request_id}/approve")
    assert response.status_code == 200
    assert client.get(f"/bookings/{created['id']}").json()["status"] == "cancelled"


def test_deny_cancel_request(client):
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")
    request_id = client.get(f"/bookings/{created['id']}").json()["request"]["id"]
    response = client.post(f"/bookings/booking-request/{request_id}/deny")
    assert response.status_code == 200
    assert response.json()["status"] == "denied"
    assert client.get(f"/bookings/{created['id']}").json()["status"] == "confirmed"


def test_approve_reschedule_request(client):
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.post(
            f"/bookings/manage-occurrence/{created['id']}/reschedule",
            json={**reschedule_payload, "tutor_id": tutor["id"]},
        )
        request_id = client.get(f"/bookings/{created['id']}").json()["request"]["id"]
        response = client.post(f"/bookings/booking-request/{request_id}/approve")
    assert response.status_code == 200
    assert client.get(f"/bookings/{created['id']}").json()["status"] == "rescheduled"


def test_approve_request_not_found(client):
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post("/bookings/booking-request/9999/approve").status_code == 404


def test_deny_request_not_found(client):
    assert client.post("/bookings/booking-request/9999/deny").status_code == 404


def test_approve_already_processed(client):
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")
        request_id = client.get(f"/bookings/{created['id']}").json()["request"]["id"]
        client.post(f"/bookings/booking-request/{request_id}/deny")
        assert client.post(f"/bookings/booking-request/{request_id}/deny").status_code == 400


# ── PUBLIC_ID REF RESOLUTION (resolve_ref) ──────────────────────────────────

def _next_occurrence_ref(first_booking: dict) -> str:
    """Given occurrence 1's response, build the ref for occurrence 2 (7 days later) —
    a genuinely virtual occurrence, since indefinite series only materialize occurrence 1
    at creation time. Booking.start is always UTC, but SQLite doesn't preserve tz-awareness,
    so the parsed value may come back naive — treat it as UTC explicitly rather than let
    .astimezone() assume the system's local timezone."""
    start = datetime.fromisoformat(first_booking["start"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    next_start = start + timedelta(days=7)
    return f"{first_booking['series_id']}:{int(next_start.timestamp())}"


def test_standalone_id_has_no_colon(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        booking = client.post("/bookings/", json=payload).json()
    assert ":" not in booking["id"]
    assert booking["series_id"] is None


def test_series_occurrence_id_matches_series_and_timestamp(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        booking = client.post("/bookings/", json=payload).json()
    assert ":" in booking["id"]
    series_part, ts_part = booking["id"].split(":")
    assert series_part == booking["series_id"]
    assert ts_part.isdigit()


def test_get_virtual_occurrence_does_not_materialize(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first_booking = client.post("/bookings/", json=payload).json()
    virtual_ref = _next_occurrence_ref(first_booking)

    response = client.get(f"/bookings/{virtual_ref}")
    assert response.status_code == 404

    # Confirm nothing was written to the DB as a side effect of the read.
    all_bookings = _all_bookings()
    assert len(all_bookings) == 1


def test_cancel_virtual_occurrence_materializes_it(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first_booking = client.post("/bookings/", json=payload).json()
        virtual_ref = _next_occurrence_ref(first_booking)

        response = client.delete(f"/bookings/{virtual_ref}")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["id"] == virtual_ref

    # Now materialized — a plain GET (which never materializes) finds it directly.
    fetched = client.get(f"/bookings/{virtual_ref}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "cancelled"

    all_bookings = _all_bookings()
    assert len(all_bookings) == 2  # occurrence 1 + the newly materialized occurrence 2


def test_action_on_virtual_occurrence_is_idempotent(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first_booking = client.post("/bookings/", json=payload).json()
        virtual_ref = _next_occurrence_ref(first_booking)

        first_cancel = client.delete(f"/bookings/{virtual_ref}")
        assert first_cancel.status_code == 200

        # Second call resolves the same (now real) row rather than materializing a duplicate —
        # it correctly 400s since the row is no longer confirmed, not because it's missing.
        second_cancel = client.delete(f"/bookings/{virtual_ref}")
    assert second_cancel.status_code == 400

    all_bookings = _all_bookings()
    assert len(all_bookings) == 2


def test_resolve_ref_unknown_series(client):
    response = client.delete("/bookings/00000000-0000-0000-0000-000000000000:1753952400")
    assert response.status_code == 404


def test_resolve_ref_invalid_timestamp(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first_booking = client.post("/bookings/", json=payload).json()
    response = client.delete(f"/bookings/{first_booking['series_id']}:not-a-timestamp")
    assert response.status_code == 400


def test_resolve_ref_rejects_occurrence_before_series_earliest(client):
    """A ref referencing a date before the series' earliest real row (correct weekday/time,
    but predating anything that ever actually existed) must not be materializable — otherwise
    anyone who knows a series' public_id could fabricate historical "confirmed" bookings."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first_booking = client.post("/bookings/", json=payload).json()
    start = datetime.fromisoformat(first_booking["start"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    earlier_start = start - timedelta(weeks=1)  # same weekday/time, one week before the series began
    fabricated_ref = f"{first_booking['series_id']}:{int(earlier_start.timestamp())}"
    response = client.delete(f"/bookings/{fabricated_ref}")
    assert response.status_code == 400
