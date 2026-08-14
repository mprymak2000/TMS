from datetime import date, datetime, timedelta, UTC
from unittest.mock import patch, MagicMock
from conftest import TestingSessionLocal
from models import Booking, BookingSeries
from schemas import BookingResponse
from routers.bookings import PAGE_SIZE, DEFAULT_PAGE_SIZE


def _all_bookings():
    """Raw DB truth (all rows, any status) — direct DB query, distinct from GET /bookings/
    (which merges in virtual occurrences and applies time_min/time_max filtering)."""
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


class _FakeBatch:
    """Minimal stand-in for google-api-python-client's BatchHttpRequest — real success responses
    for each added request, so batched-delete code paths are genuinely exercised in tests
    instead of silently no-op'd by a generic MagicMock."""
    def __init__(self, callback=None):
        self._callback = callback
        self._requests = []

    def add(self, request, request_id=None, callback=None):
        self._requests.append((request_id, callback or self._callback))
        return self

    def execute(self):
        for request_id, cb in self._requests:
            if cb:
                cb(request_id, {}, None)


def mock_calendar_service():
    svc = MagicMock()
    svc.events().insert().execute.return_value = {"id": MOCK_EVENT_ID}
    svc.events().delete().execute.return_value = {}
    svc.events().patch().execute.return_value = {"id": MOCK_EVENT_ID}
    svc.events().get().execute.return_value = {"id": MOCK_EVENT_ID, "summary": "old"}
    svc.events().instances().execute.return_value = {"items": [{"id": MOCK_INSTANCE_ID, "start": {"dateTime": "2099-06-10T20:00:00Z"}, "end": {"dateTime": "2099-06-10T21:30:00Z"}}]}
    svc.new_batch_http_request.side_effect = lambda callback=None: _FakeBatch(callback)
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
    pending = client.get("/bookings/?pending_only=true").json()["items"]
    assert len(pending) == 1
    assert pending[0]["request"]["type"] == "cancel_occurrence"
    assert pending[0]["request"]["status"] == "pending"


def test_pending_only_ignores_time_range_params(client):
    """pending_only has no time dimension — time_min/time_max are silently irrelevant there,
    not an error and don't change the result. Deliberately passing an inverted range
    (time_min after time_max) to prove they're truly ignored, not just coincidentally satisfied."""
    tutor, event_type = setup_strict_notice(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")

    items = client.get("/bookings/?pending_only=true&time_min=2099-01-01T00:00:00&time_max=2000-01-01T00:00:00").json()["items"]
    assert len(items) == 1
    assert items[0]["request"]["type"] == "cancel_occurrence"


def test_pending_only_has_more_and_page_size(client):
    tutor, event_type = setup_strict_notice(client)
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        for i in range(3):
            payload = {
                **booking_payload,
                "tutor_id": tutor["id"],
                "event_type_id": event_type["id"],
                "start": f"2099-06-{10 + i:02d}T16:00:00",
                "end": f"2099-06-{10 + i:02d}T17:00:00",
            }
            created = client.post("/bookings/", json=payload).json()
            client.post(f"/bookings/manage-occurrence/{created['id']}/cancel")

    page1 = client.get("/bookings/?pending_only=true&page=1&page_size=2").json()
    assert page1["has_more"] is True
    assert page1["page_size"] == 2
    assert len(page1["items"]) == 2

    page2 = client.get("/bookings/?pending_only=true&page=2&page_size=2").json()
    assert page2["has_more"] is False
    assert len(page2["items"]) == 1


def test_my_bookings_include_cancelled(client):
    """include_cancelled is an independent filter, not tied to time direction — default excludes
    cancelled rows (mirrors Google Calendar's showDeleted), opt-in shows everything."""
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.delete(f"/bookings/{created['id']}")

    default_items = client.get(f"/bookings/?email={created['student_email']}").json()["items"]
    assert created["id"] not in [i["id"] for i in default_items]

    all_items = client.get(f"/bookings/?email={created['student_email']}&include_cancelled=true").json()["items"]
    assert created["id"] in [i["id"] for i in all_items]


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


def test_admin_cancel_ignores_not_allowed_policy(client):
    """Admin's DELETE /{ref} deliberately ignores event-type cancel_mode policy — only the
    booking's own confirmed status is enforced. Policy (and the past-time floor) are booker-
    facing rules that don't apply to admin, who needs to be able to override both."""
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={"name": "No Cancel ET", "duration_minutes": 60, "recurring": False, "cancel_mode": "not_allowed", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        assert client.delete(f"/bookings/{created['id']}").status_code == 200


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


def test_admin_reschedule_ignores_not_allowed_policy(client):
    """Admin's POST /{ref}/reschedule deliberately ignores event-type reschedule_mode policy —
    same reasoning as test_admin_cancel_ignores_not_allowed_policy above."""
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={"name": "No Reschedule ET", "duration_minutes": 60, "recurring": False, "reschedule_mode": "not_allowed", "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        original = client.post("/bookings/", json=payload).json()
        assert client.post(f"/bookings/{original['id']}/reschedule", json={**reschedule_payload, "tutor_id": tutor["id"]}).status_code == 200


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


def test_cancel_series_batches_instance_deletes(client):
    """Deleting more than one batch's worth of instances (50) must chunk into multiple batch
    requests rather than one oversized batch or falling back to per-instance calls — this is
    the fix for cancel-series hanging on an indefinite series with many future instances."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    many_instances = {"items": [
        {"id": f"inst-{i}", "start": {"dateTime": "2099-06-10T20:00:00Z"}, "end": {"dateTime": "2099-06-10T21:30:00Z"}}
        for i in range(120)
    ]}
    svc = mock_calendar_service()
    svc.events().instances().execute.return_value = many_instances
    with patch("routers.bookings.get_calendar_service", return_value=svc):
        first = client.post("/bookings/", json=payload).json()
        response = client.delete(f"/bookings/booking-series/{first['series_id']}")
    assert response.status_code == 200
    # 120 instances / 50 per batch = 3 batch calls, not 120 individual delete() calls
    assert svc.new_batch_http_request.call_count == 3


def test_cancel_series_not_found(client):
    assert client.delete("/bookings/booking-series/9999").status_code == 404


def test_cancel_already_cancelled_series(client):
    """Previously missing entirely — DELETE /booking-series/{id} didn't check is_active at all,
    so cancelling an already-cancelled series would silently re-run the whole cancel saga."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        first = client.post("/bookings/", json=payload).json()
        series_id = first["series_id"]
        assert client.delete(f"/bookings/booking-series/{series_id}").status_code == 200
        assert client.delete(f"/bookings/booking-series/{series_id}").status_code == 400


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

    response = client.get(f"/bookings/?email={created['student_email']}&time_min=2099-01-01T00:00:00&page=1")
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
    items = client.get("/bookings/?email=someone-else@example.com&time_min=2099-01-01T00:00:00").json()["items"]
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
    items = client.get("/bookings/?time_min=2099-01-01T00:00:00").json()["items"]
    emails = {i["student_email"] for i in items}
    assert "alex@example.com" in emails
    assert "someone-else@example.com" in emails
    assert any(":" in i["id"] for i in items)  # includes virtual occurrences, not just real rows


def test_my_bookings_tutor_ids_filters_scope(client):
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_standalone, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_standalone, "name": "One-off Session B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"})
    items = client.get(f"/bookings/?tutor_ids={tutor_a['id']}&time_min=2099-01-01T00:00:00").json()["items"]
    assert len(items) == 1
    assert items[0]["tutor_id"] == tutor_a["id"]

    # multi-value: both tutors requested at once returns both bookings
    items = client.get(f"/bookings/?tutor_ids={tutor_a['id']}&tutor_ids={tutor_b['id']}&time_min=2099-01-01T00:00:00").json()["items"]
    assert {i["tutor_id"] for i in items} == {tutor_a["id"], tutor_b["id"]}


def test_my_bookings_event_type_ids_filters_scope(client):
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_standalone, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_standalone, "name": "One-off Session B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"})
    items = client.get(f"/bookings/?event_type_ids={event_type_a['id']}&time_min=2099-01-01T00:00:00").json()["items"]
    assert len(items) == 1
    assert items[0]["event_type_id"] == event_type_a["id"]

    items = client.get(f"/bookings/?event_type_ids={event_type_a['id']}&event_type_ids={event_type_b['id']}&time_min=2099-01-01T00:00:00").json()["items"]
    assert {i["event_type_id"] for i in items} == {event_type_a["id"], event_type_b["id"]}


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

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    upcoming = client.get(f"/bookings/?email={created['student_email']}&time_min={now_iso}").json()["items"]
    past = client.get(f"/bookings/?email={created['student_email']}&time_max={now_iso}").json()["items"]
    assert created["id"] not in [i["id"] for i in upcoming]
    assert created["id"] in [i["id"] for i in past]


def test_my_bookings_never_writes_to_db(client):
    """Browsing a page deep enough to require many virtual occurrences must not materialize any of them."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.get(f"/bookings/?email={created['student_email']}&time_min=2099-01-01T00:00:00&page=3")
    all_bookings = _all_bookings()
    assert len(all_bookings) == 1  # only the real occurrence 1 — nothing materialized by browsing


def test_my_bookings_no_bounds_returns_since_inception(client):
    """Omitting time_min entirely must not implicitly default to 'now' — it means unbounded,
    starting from the series' actual start_date, however far in the past that is."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    db = TestingSessionLocal()
    series = db.query(BookingSeries).filter(BookingSeries.public_id == created["series_id"]).first()
    series.start_date = date(2020, 1, 8)  # well before "now" and far before the booking's 2099 start
    db.commit()
    db.close()

    items = client.get(f"/bookings/?email={created['student_email']}").json()["items"]
    assert items[0]["start"] < "2021-01-01"  # earliest item comes from 2020, not "now" (~2026)


def test_my_bookings_time_max_stops_virtual_generation(client):
    """time_max, when given, stops virtual generation early — independent of page/page_size."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    # weekly occurrences land on 06-10 (real), 06-17, 06-24, 07-01, ... — time_max cuts off after 06-24
    items = client.get(f"/bookings/?email={created['student_email']}&time_max=2099-06-24T23:59:59").json()["items"]
    assert len(items) == 3
    assert items[-1]["start"].startswith("2099-06-24")


def test_my_bookings_bounded_range_paginates_with_total_count(client):
    """A bounded range (both time_min and time_max) still paginates normally via page/page_size —
    it's not "return everything." What's different is the range is cheap to walk to completion
    (time_max alone guarantees termination), so the true total is known and surfaced via
    X-Total-Count, letting the frontend render real page navigation instead of incremental
    Load More."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    # weekly from 06-10 through 08-26 = 12 occurrences, more than PAGE_SIZE
    base = f"/bookings/?email={created['student_email']}&time_min=2099-06-01T00:00:00&time_max=2099-08-26T23:59:59&page_size={PAGE_SIZE}"
    page1 = client.get(f"{base}&page=1").json()
    assert page1["total"] == 12
    items1 = page1["items"]
    assert len(items1) == PAGE_SIZE
    assert items1[0]["start"].startswith("2099-06-10")

    page2 = client.get(f"{base}&page=2").json()
    assert page2["total"] == 12
    items2 = page2["items"]
    assert len(items2) == 12 - PAGE_SIZE
    assert items2[-1]["start"].startswith("2099-08-26")


def test_my_bookings_time_max_only_still_gets_total_count(client):
    """time_max alone is enough to guarantee termination (the walk's floor is always a real
    anchor — series.start_date when time_min is omitted), so a time_max-only query is just as
    safely bounded as a fully-specified range and should get the same real total, not fall back
    to the unbounded/Load-More path. Same 12-occurrence series as the fully-bounded test above,
    with time_min dropped entirely (the series itself starts 2099-06-10, same effective floor)."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    response = client.get(f"/bookings/?email={created['student_email']}&time_max=2099-08-26T23:59:59&page=1&page_size={PAGE_SIZE}").json()
    assert response["total"] == 12
    assert len(response["items"]) == PAGE_SIZE


def test_my_bookings_default_page_size_returns_all_in_one_page(client):
    """page_size defaults to 250 (DEFAULT_PAGE_SIZE) when omitted — a naturally-bounded window
    (Day/Week/Month) gets everything in one page without needing to ask for it explicitly."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    response = client.get(f"/bookings/?email={created['student_email']}&time_max=2099-08-26T23:59:59&page=1").json()
    assert response["total"] == 12
    assert response["page_size"] == DEFAULT_PAGE_SIZE
    assert len(response["items"]) == 12


def test_my_bookings_page_size_rejects_out_of_range(client):
    assert client.get("/bookings/?page_size=0").status_code == 422
    assert client.get("/bookings/?page_size=501").status_code == 422


def test_my_bookings_unbounded_has_no_total(client):
    """Unbounded queries (plain Upcoming/Past) never compute a total — only meaningful for a
    genuinely bounded range."""
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json=payload)

    response = client.get("/bookings/?time_min=2099-01-01T00:00:00").json()
    assert response["total"] is None


def test_my_bookings_time_min_narrows_real_rows(client):
    """time_min narrows real rows too, not just virtual generation — a genuinely new capability,
    since the floor used to be hardcoded to 'now'."""
    tutor, event_type = setup_standalone(client)
    early_payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    late_payload = {**early_payload, "start": "2099-08-10T16:00:00", "end": "2099-08-10T17:30:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        early = client.post("/bookings/", json=early_payload).json()
        late = client.post("/bookings/", json=late_payload).json()

    items = client.get(f"/bookings/?email={early['student_email']}&time_min=2099-07-01T00:00:00").json()["items"]
    ids = [i["id"] for i in items]
    assert early["id"] not in ids
    assert late["id"] in ids


def test_my_bookings_order_desc_paginates_from_most_recent(client):
    """order=desc must fetch the most-recent-first page, not just display-reverse whatever the
    oldest-first page happened to be. Regression test for a real bug: an unbounded-below query
    (time_max=now, no time_min) always paginated starting from the earliest matching row —
    reversing an already-fetched page can't fix that, since it doesn't change which rows got
    fetched in the first place. 15 past bookings, more than PAGE_SIZE, so the bug (fetching the
    oldest page instead of the most recent) would actually be visible here."""
    tutor, event_type = setup_standalone(client)
    created_ids = []
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        for i in range(15):
            payload = {
                **booking_payload,
                "tutor_id": tutor["id"],
                "event_type_id": event_type["id"],
                "start": f"2099-06-{10 + i:02d}T16:00:00",
                "end": f"2099-06-{10 + i:02d}T17:00:00",
            }
            created_ids.append(client.post("/bookings/", json=payload).json()["id"])

    db = TestingSessionLocal()
    for i, booking_id in enumerate(created_ids):
        booking = db.query(Booking).filter(Booking.public_id == booking_id).first()
        booking.start = datetime(2020, 1, 1 + i, 16, 0, tzinfo=UTC)  # 2020-01-01 .. 2020-01-15
        booking.end = datetime(2020, 1, 1 + i, 17, 0, tzinfo=UTC)
    db.commit()
    db.close()

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    items = client.get(f"/bookings/?time_max={now_iso}&order=desc&page=1&page_size={PAGE_SIZE}").json()["items"]
    assert len(items) == PAGE_SIZE
    assert items[0]["start"].startswith("2020-01-15")  # most recent of the 15, comes first
    assert items[-1]["start"].startswith(f"2020-01-{16 - PAGE_SIZE:02d}")  # PAGE_SIZE-th most recent


def test_my_bookings_order_rejects_invalid_value(client):
    assert client.get("/bookings/?order=sideways").status_code == 400


def test_virtual_occurrences_anchored_to_start_date_not_earliest_booking(client):
    """The lower bound for virtual generation must come from series.start_date, not from
    scanning for the earliest surviving real row — proven by deleting that row entirely."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    db = TestingSessionLocal()
    db.query(Booking).filter(Booking.public_id == created["id"]).delete()
    db.commit()
    db.close()
    assert _all_bookings() == []  # no real rows left for this series at all

    items = client.get(f"/bookings/?email={created['student_email']}").json()["items"]
    assert len(items) > 0
    assert items[0]["start"].startswith("2099-06-10")  # still anchored correctly, purely virtual now


def test_rescheduled_occurrence_not_double_counted(client):
    """Rescheduling one occurrence must not leave a phantom virtual duplicate at the original
    slot, and must not disrupt virtual generation for the following week."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        series_id = created["series_id"]

        # occurrence 2 (06-17, 16:00 America/New_York = 20:00 UTC) is virtual until acted on by ref
        ref = f"{series_id}:{int(datetime(2099, 6, 17, 20, 0, tzinfo=UTC).timestamp())}"
        reschedule_body = {**reschedule_payload, "tutor_id": tutor["id"], "start": "2099-06-18T16:00:00", "end": "2099-06-18T17:30:00"}
        rescheduled = client.post(f"/bookings/{ref}/reschedule", json=reschedule_body).json()

        items = client.get(f"/bookings/?email={created['student_email']}&time_min=2099-01-01T00:00:00").json()["items"]

    starts = [i["start"] for i in items]
    ids = [i["id"] for i in items]
    assert not any(s.startswith("2099-06-17") for s in starts)  # original slot gone entirely
    assert ids.count(rescheduled["id"]) == 1  # new slot appears exactly once
    assert any(s.startswith("2099-06-24") for s in starts)  # following week's occurrence still generates


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


# ── BOOKING-SERIES LIST + PER-SERIES OCCURRENCES ────────────────────────────────

def test_get_booking_series_lists_active_series(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    series_list = client.get("/bookings/booking-series").json()["items"]
    assert len(series_list) == 1
    assert series_list[0]["id"] == created["series_id"]


def test_get_booking_series_excludes_standalone(client):
    tutor, event_type = setup_standalone(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json=payload)

    assert client.get("/bookings/booking-series").json()["items"] == []


def test_get_booking_series_excludes_cancelled_series(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.delete(f"/bookings/booking-series/{created['series_id']}")

    assert client.get("/bookings/booking-series").json()["items"] == []


def test_get_booking_series_email_filter(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_recurring, "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    other_payload = {**payload, "student_email": "someone-else@example.com", "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json=payload)
        client.post("/bookings/", json=other_payload)

    items = client.get("/bookings/booking-series?email=someone-else@example.com").json()["items"]
    assert len(items) == 1
    assert items[0]["student_email"] == "someone-else@example.com"


def test_get_booking_series_tutor_ids_filters_scope(client):
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_recurring, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_recurring, "name": "Tutoring Session B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created_a = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]}).json()
        created_b = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"}).json()

    items = client.get(f"/bookings/booking-series?tutor_ids={tutor_a['id']}").json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == created_a["series_id"]

    # multi-value: both tutors requested at once returns both series
    items = client.get(f"/bookings/booking-series?tutor_ids={tutor_a['id']}&tutor_ids={tutor_b['id']}").json()["items"]
    assert {i["id"] for i in items} == {created_a["series_id"], created_b["series_id"]}


def test_get_booking_series_event_type_ids_filters_scope(client):
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_recurring, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_recurring, "name": "Tutoring Session B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created_a = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]}).json()
        created_b = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"}).json()

    items = client.get(f"/bookings/booking-series?event_type_ids={event_type_a['id']}").json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == created_a["series_id"]

    items = client.get(f"/bookings/booking-series?event_type_ids={event_type_a['id']}&event_type_ids={event_type_b['id']}").json()["items"]
    assert {i["id"] for i in items} == {created_a["series_id"], created_b["series_id"]}


def test_booking_series_occurrences_upcoming_merges_real_and_virtual(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    response = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_min=2099-01-01T00:00:00&page=1")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 2  # occurrence 1 (real) + at least one virtual occurrence
    assert items[0]["id"] == created["id"]
    starts = [i["start"] for i in items]
    assert starts == sorted(starts)


def test_booking_series_occurrences_never_writes_to_db(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_min=2099-01-01T00:00:00&page=3")
    all_bookings = _all_bookings()
    assert len(all_bookings) == 1  # only the real occurrence 1 — nothing materialized by browsing


def test_booking_series_occurrences_past(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    db = TestingSessionLocal()
    booking = db.query(Booking).filter(Booking.public_id == created["id"]).first()
    booking.start = datetime(2020, 1, 7, 16, 0, tzinfo=UTC)
    booking.end = datetime(2020, 1, 7, 17, 0, tzinfo=UTC)
    # series.start_date must move too — otherwise the series still legitimately claims to start
    # in 2099, the floor stays clamped there regardless of time_min, and virtual generation
    # regenerates a phantom duplicate of the now-orphaned 2099 slot (existing_starts no longer
    # contains it once the row's start moved away from it).
    series = db.query(BookingSeries).filter(BookingSeries.id == booking.series_id).first()
    series.start_date = date(2020, 1, 7)
    db.commit()
    db.close()

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    upcoming = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_min={now_iso}").json()["items"]
    past = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_max={now_iso}").json()["items"]
    assert created["id"] not in [i["id"] for i in upcoming]
    assert created["id"] in [i["id"] for i in past]


def test_booking_series_occurrences_not_found(client):
    response = client.get("/bookings/booking-series/00000000-0000-0000-0000-000000000000/occurrences")
    assert response.status_code == 404


def test_booking_series_occurrences_excludes_other_series(client):
    tutor, availability = make_tutor_with_schedule(client)
    event_type = client.post("/event_types/", json={**event_type_recurring, "availability": availability}).json()
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    other_payload = {**payload, "student_email": "someone-else@example.com", "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()
        other_created = client.post("/bookings/", json=other_payload).json()

    items = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_min=2099-01-01T00:00:00").json()["items"]
    ids = [i["id"] for i in items]
    assert created["id"] in ids
    assert other_created["id"] not in ids


def test_booking_series_occurrences_no_bounds_returns_since_inception(client):
    """Same guarantee as the flat list endpoint: omitting time_min doesn't default to 'now'."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    db = TestingSessionLocal()
    series = db.query(BookingSeries).filter(BookingSeries.public_id == created["series_id"]).first()
    series.start_date = date(2020, 1, 8)
    db.commit()
    db.close()

    items = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences").json()["items"]
    assert items[0]["start"] < "2021-01-01"


def test_booking_series_occurrences_time_max_stops_virtual_generation(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    items = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_max=2099-06-24T23:59:59").json()["items"]
    assert len(items) == 3
    assert items[-1]["start"].startswith("2099-06-24")


def test_booking_series_occurrences_bounded_range_returns_total(client):
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    # weekly from 06-10 through 08-26 = 12 occurrences, more than PAGE_SIZE (10)
    response = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_min=2099-06-01T00:00:00&time_max=2099-08-26T23:59:59").json()
    assert response["total"] == 12
    assert len(response["items"]) == 10


def test_booking_series_occurrences_time_min_narrows(client):
    """time_min excludes an earlier real occurrence while keeping a later virtual one."""
    tutor, event_type = setup_recurring(client)
    payload = {**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created = client.post("/bookings/", json=payload).json()

    items = client.get(f"/bookings/booking-series/{created['series_id']}/occurrences?time_min=2099-06-15T00:00:00").json()["items"]
    ids = [i["id"] for i in items]
    starts = [i["start"] for i in items]
    assert created["id"] not in ids  # occurrence 1 (06-10) excluded, before time_min
    assert any(s.startswith("2099-06-17") for s in starts)  # occurrence 2 included


# ── FACETS ────────────────────────────────────────────────────────────────────

def test_list_bookings_facets_self_exclusion(client):
    """Selecting Tutor A must not remove Tutor B from facets.tutors (self-exclusion) — but
    should narrow facets.event_types/facets.students down to only what A actually has."""
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_standalone, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_standalone, "name": "One-off Session B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"], "student_first": "Alice", "student_last": "Smith"})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "student_first": "Bob", "student_last": "Jones", "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"})

    body = client.get(f"/bookings/?tutor_ids={tutor_a['id']}&time_min=2099-01-01T00:00:00").json()
    tutor_ids_in_facets = {t["id"] for t in body["facets"]["tutors"]}
    assert tutor_ids_in_facets == {tutor_a["id"], tutor_b["id"]}  # self-exclusion: both still shown

    event_type_ids_in_facets = {e["id"] for e in body["facets"]["event_types"]}
    assert event_type_ids_in_facets == {event_type_a["id"]}  # narrowed by tutor_ids

    students_in_facets = {(s["first_name"], s["last_name"]) for s in body["facets"]["students"]}
    assert students_in_facets == {("Alice", "Smith")}


def test_list_bookings_student_filter_exact_pair_match(client):
    """tuple_ matching must not cross-match — filtering for John Smith must not also return
    a different John (Doe), even though they share a first name."""
    tutor, event_type = setup_standalone(client)
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"], "student_first": "John", "student_last": "Smith"})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor["id"], "event_type_id": event_type["id"], "student_first": "John", "student_last": "Doe", "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"})

    items = client.get("/bookings/?student=John%7CSmith&time_min=2099-01-01T00:00:00").json()["items"]
    assert len(items) == 1
    assert items[0]["student_first"] == "John"
    assert items[0]["student_last"] == "Smith"


def test_list_bookings_facets_exclude_options_with_no_matches_in_window(client):
    """A tutor whose only booking falls outside the queried time window must not appear in
    facets.tutors — facets reflect the current query scope, not the whole business."""
    tutor_in, availability_in = make_tutor_with_schedule(client)
    tutor_out = client.post("/tutors/", json={**tutor_payload, "last_name": "Outside"}).json()
    schedule_out = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_out["id"]}).json()
    availability_out = [{"tutor_id": tutor_out["id"], "schedule_id": schedule_out["id"]}]
    event_type_in = client.post("/event_types/", json={**event_type_standalone, "availability": availability_in}).json()
    event_type_out = client.post("/event_types/", json={**event_type_standalone, "name": "Outside ET", "availability": availability_out}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_in["id"], "event_type_id": event_type_in["id"]})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_out["id"], "event_type_id": event_type_out["id"], "start": "2099-12-25T16:00:00", "end": "2099-12-25T17:00:00"})

    body = client.get("/bookings/?time_min=2099-01-01T00:00:00&time_max=2099-08-01T00:00:00").json()
    tutor_ids_in_facets = {t["id"] for t in body["facets"]["tutors"]}
    assert tutor_ids_in_facets == {tutor_in["id"]}


def test_pending_only_facets_self_exclusion(client):
    """Requests-tab facets follow the same self-exclusion rule as the main list."""
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_strict_notice, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_strict_notice, "name": "Strict B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        created_a = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]}).json()
        client.post(f"/bookings/manage-occurrence/{created_a['id']}/cancel")
        created_b = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"}).json()
        client.post(f"/bookings/manage-occurrence/{created_b['id']}/cancel")

    body = client.get(f"/bookings/?pending_only=true&tutor_ids={tutor_a['id']}").json()
    assert len(body["items"]) == 1
    tutor_ids_in_facets = {t["id"] for t in body["facets"]["tutors"]}
    assert tutor_ids_in_facets == {tutor_a["id"], tutor_b["id"]}  # self-exclusion


def test_get_booking_series_facets_self_exclusion(client):
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_recurring, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_recurring, "name": "Tutoring B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]})
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"})

    body = client.get(f"/bookings/booking-series?tutor_ids={tutor_a['id']}").json()
    tutor_ids_in_facets = {t["id"] for t in body["facets"]["tutors"]}
    assert tutor_ids_in_facets == {tutor_a["id"], tutor_b["id"]}  # self-exclusion
    event_type_ids_in_facets = {e["id"] for e in body["facets"]["event_types"]}
    assert event_type_ids_in_facets == {event_type_a["id"]}


def test_get_booking_series_facets_exclude_cancelled_series(client):
    """A cancelled series must not contribute to facets, same as it's already excluded from items."""
    tutor_a, availability_a = make_tutor_with_schedule(client)
    tutor_b = client.post("/tutors/", json={**tutor_payload, "last_name": "Other"}).json()
    schedule_b = client.post("/schedules/", json={**_schedule, "tutor_id": tutor_b["id"]}).json()
    availability_b = [{"tutor_id": tutor_b["id"], "schedule_id": schedule_b["id"]}]
    event_type_a = client.post("/event_types/", json={**event_type_recurring, "availability": availability_a}).json()
    event_type_b = client.post("/event_types/", json={**event_type_recurring, "name": "Tutoring B", "availability": availability_b}).json()
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_a["id"], "event_type_id": event_type_a["id"]})
        created_b = client.post("/bookings/", json={**booking_payload, "tutor_id": tutor_b["id"], "event_type_id": event_type_b["id"], "start": "2099-06-11T16:00:00", "end": "2099-06-11T17:30:00"}).json()
        client.delete(f"/bookings/booking-series/{created_b['series_id']}")

    body = client.get("/bookings/booking-series").json()
    tutor_ids_in_facets = {t["id"] for t in body["facets"]["tutors"]}
    assert tutor_ids_in_facets == {tutor_a["id"]}
