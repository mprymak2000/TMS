"""BookingLink status lifecycle: active | paused | archived.

Covers the two guards and the one invariant that matters most — a series is its own booking
template and keeps running whatever its link's status is.
"""
from unittest.mock import patch

from conftest import TestingSessionLocal
from models import Booking, BookingSeries
from test_bookings import (
    booking_link_recurring,
    booking_link_standalone,
    booking_payload,
    make_tutor_with_schedule,
    mock_calendar_service,
)


def _make_booking(client, link, tutor):
    payload = {**booking_payload, "tutor_id": tutor["id"], "booking_link_id": link["id"]}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        return client.post("/bookings/", json=payload)


def _setup(client, link_payload=booking_link_standalone):
    """Returns availability too — tutors are unique by name, so extra links must reuse it."""
    tutor, availability = make_tutor_with_schedule(client)
    link = client.post("/booking_links/", json={**link_payload, "availability": availability}).json()
    return tutor, link, availability


def _make_link(client, availability, **overrides):
    return client.post("/booking_links/", json={**booking_link_standalone, **overrides, "availability": availability})


# ── STATE TRANSITIONS ───────────────────────────────────────────────────────

def test_pause_and_resume_round_trip(client):
    _, link, _ = _setup(client)
    assert client.post(f"/booking_links/{link['id']}/pause").json()["status"] == "paused"
    assert client.post(f"/booking_links/{link['id']}/resume").json()["status"] == "active"


def test_archive_is_terminal(client):
    _, link, _ = _setup(client)
    archived = client.delete(f"/booking_links/{link['id']}").json()
    assert archived["status"] == "archived"
    assert archived["archived_at"] is not None
    # no restore: neither resume nor pause can bring it back
    assert client.post(f"/booking_links/{link['id']}/resume").status_code == 400
    assert client.post(f"/booking_links/{link['id']}/pause").status_code == 400
    assert client.delete(f"/booking_links/{link['id']}").status_code == 409


def test_paused_links_stay_listed_archived_do_not(client):
    _, paused, availability = _setup(client)
    archived = _make_link(client, availability, slug="second-link").json()
    client.post(f"/booking_links/{paused['id']}/pause")
    client.delete(f"/booking_links/{archived['id']}")

    listed = {l["id"] for l in client.get("/booking_links/").json()}
    assert paused["id"] in listed
    assert archived["id"] not in listed
    assert archived["id"] in {l["id"] for l in client.get("/booking_links/?include_archived=true").json()}


# ── GUARD 1: must be bookable (blocks paused AND archived, distinct reasons) ──

def test_create_blocked_on_paused_with_its_own_message(client):
    tutor, link, _ = _setup(client)
    client.post(f"/booking_links/{link['id']}/pause")
    response = _make_booking(client, link, tutor)
    assert response.status_code == 400
    assert "paused" in response.json()["detail"].lower()


def test_create_blocked_on_archived_with_its_own_message(client):
    tutor, link, _ = _setup(client)
    client.delete(f"/booking_links/{link['id']}")
    response = _make_booking(client, link, tutor)
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "no longer offered" in detail and "paused" not in detail


# ── GUARD 2: must not be archived (paused passes) ───────────────────────────

def test_reschedule_still_works_on_paused_link(client):
    tutor, link, _ = _setup(client)
    booking = _make_booking(client, link, tutor).json()
    client.post(f"/booking_links/{link['id']}/pause")

    body = {"tutor_id": tutor["id"], "start": "2099-06-17T16:00:00", "end": "2099-06-17T17:30:00",
            "timezone": "America/New_York"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post(f"/bookings/{booking['id']}/reschedule", json=body)
    assert response.status_code == 200


def test_reschedule_blocked_on_archived_link(client):
    tutor, link, _ = _setup(client)
    booking = _make_booking(client, link, tutor).json()
    client.delete(f"/booking_links/{link['id']}")

    body = {"tutor_id": tutor["id"], "start": "2099-06-17T16:00:00", "end": "2099-06-17T17:30:00",
            "timezone": "America/New_York"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        response = client.post(f"/bookings/{booking['id']}/reschedule", json=body)
    assert response.status_code == 400
    assert "archived" in response.json()["detail"].lower()


def test_cancel_still_works_on_archived_link(client):
    """Policy is frozen on the booking, so cancelling never needs the link."""
    tutor, link, _ = _setup(client)
    booking = _make_booking(client, link, tutor).json()
    client.delete(f"/booking_links/{link['id']}")
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.delete(f"/bookings/{booking['id']}").status_code == 200


def test_link_edit_blocked_only_when_archived(client):
    _, link, availability = _setup(client)
    body = {**booking_link_standalone, "duration_minutes": 45, "availability": availability}

    client.post(f"/booking_links/{link['id']}/pause")
    assert client.put(f"/booking_links/{link['id']}", json=body).status_code == 200

    client.post(f"/booking_links/{link['id']}/resume")
    client.delete(f"/booking_links/{link['id']}")
    assert client.put(f"/booking_links/{link['id']}", json=body).status_code == 403


# ── SLUGS: released by archive, held by pause ───────────────────────────────

def test_archive_releases_slug_for_reuse(client):
    _, link, availability = _setup(client)
    assert _make_link(client, availability).status_code == 409

    client.delete(f"/booking_links/{link['id']}")
    reused = _make_link(client, availability)
    assert reused.status_code == 201
    # both rows now hold the same slug; only the active one routes
    assert client.get(f"/booking_links/slug/{booking_link_standalone['slug']}").json()["id"] == reused.json()["id"]


def test_pause_does_not_release_slug(client):
    _, link, availability = _setup(client)
    client.post(f"/booking_links/{link['id']}/pause")
    assert _make_link(client, availability).status_code == 409


def test_slug_lookup_resolves_paused_but_not_archived(client):
    """Paused resolves so the page can explain itself; archived 404s outright."""
    _, link, _ = _setup(client)
    slug = booking_link_standalone["slug"]

    client.post(f"/booking_links/{link['id']}/pause")
    assert client.get(f"/booking_links/slug/{slug}").json()["status"] == "paused"

    client.post(f"/booking_links/{link['id']}/resume")
    client.delete(f"/booking_links/{link['id']}")
    assert client.get(f"/booking_links/slug/{slug}").status_code == 404


# ── SERIES ARE INDEPENDENT OF LINK STATUS ──────────────────────────────────

def test_series_survives_link_archive(client):
    """A series is its own booking template — archiving its link changes nothing about it."""
    tutor, link, availability = _setup(client, booking_link_recurring)
    _make_booking(client, link, tutor)
    with TestingSessionLocal() as db:
        before = db.query(Booking).filter(Booking.status == "confirmed").count()
        series = db.query(BookingSeries).one()
        assert series.status is None and series.until is None  # active, indefinite

    client.delete(f"/booking_links/{link['id']}")

    with TestingSessionLocal() as db:
        series = db.query(BookingSeries).one()
        assert series.status is None            # not cancelled
        assert series.until is None             # not truncated
        assert series.booking_link_id == link["id"]  # FK intact, never nulled
        assert db.query(Booking).filter(Booking.status == "confirmed").count() == before


# ── REASSIGNMENT: the repair path ──────────────────────────────────────────

def test_reassign_restores_reschedule_after_archive(client):
    tutor, link, availability = _setup(client)
    booking = _make_booking(client, link, tutor).json()
    client.delete(f"/booking_links/{link['id']}")

    replacement = _make_link(client, availability, slug="replacement").json()

    body = {"tutor_id": tutor["id"], "start": "2099-06-17T16:00:00", "end": "2099-06-17T17:30:00",
            "timezone": "America/New_York"}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post(f"/bookings/{booking['id']}/reschedule", json=body).status_code == 400

    assert client.post(f"/bookings/{booking['id']}/reassign?booking_link_id={replacement['id']}").status_code == 200

    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        assert client.post(f"/bookings/{booking['id']}/reschedule", json=body).status_code == 200


def test_cannot_reassign_to_an_archived_link(client):
    tutor, link, availability = _setup(client)
    booking = _make_booking(client, link, tutor).json()
    dead = _make_link(client, availability, slug="dead-link").json()
    client.delete(f"/booking_links/{dead['id']}")

    response = client.post(f"/bookings/{booking['id']}/reassign?booking_link_id={dead['id']}")
    assert response.status_code == 400
    assert "archived" in response.json()["detail"].lower()


# ── SLUG FORMAT ────────────────────────────────────────────────────────────

def test_slug_format_is_enforced_server_side(client):
    """The frontend slugifies as you type, but that's a convenience — a slug lands in a URL."""
    _, availability = make_tutor_with_schedule(client)
    for bad in ["My Link", "Trailing-", "-leading", "double--hyphen", "UPPER", "punct!"]:
        r = client.post("/booking_links/", json={**booking_link_standalone, "slug": bad, "availability": availability})
        assert r.status_code == 422, f"{bad!r} should be rejected, got {r.status_code}"
    ok = client.post("/booking_links/", json={**booking_link_standalone, "slug": "8-week-course", "availability": availability})
    assert ok.status_code == 201
