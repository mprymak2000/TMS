"""Booking types — the "kind" label.

The property worth protecting is that a type is a *row* and bookings hold a *pointer* to it. That
combination is what makes a rename a correction (every booking pointing at it relabels, past ones
included) while a link switching its type reaches only future bookings. A frozen string column would
give the second and not the first, which is why it was rejected.
"""
from datetime import UTC, datetime
from unittest.mock import patch

from conftest import TestingSessionLocal
from models import Booking, BookingLink, BookingSeries
from tests.test_bookings import (
    booking_payload,
    make_tutor_with_schedule,
    mock_calendar_service,
    _next_occurrence_ref,
    booking_link_recurring,
    booking_link_standalone,
)


def _type(client, label, color="#6366f1"):
    return client.post("/booking_types/", json={"label": label, "color": color}).json()


def _link(client, availability, slug, booking_type_id, recurring=False):
    base = booking_link_recurring if recurring else booking_link_standalone
    return client.post("/booking_links/", json={
        **base, "slug": slug, "booking_type_id": booking_type_id, "availability": availability,
    }).json()


def _book(client, tutor, link, **overrides):
    payload = {**booking_payload, "tutor_id": tutor["id"], "booking_link_id": link["id"], **overrides}
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        return client.post("/bookings/", json=payload).json()


WINDOW = "time_min=2099-01-01T00:00:00Z&time_max=2099-12-31T00:00:00Z"


# ── CRUD ────────────────────────────────────────────────────────────────────

def test_label_is_unique_case_insensitively(client):
    _type(client, "Consultation")
    assert client.post("/booking_types/", json={"label": "consultation"}).status_code == 409


def test_rename_collision_ignores_the_row_being_renamed(client):
    t = _type(client, "Consultation")
    # Recolouring while keeping the same label must not collide with itself.
    assert client.put(f"/booking_types/{t['id']}", json={"label": "Consultation", "color": "#ef4444"}).status_code == 200


# ── THE POINT OF THE TABLE ──────────────────────────────────────────────────

def test_renaming_a_type_relabels_past_bookings(client):
    """The reason this is a table and not a string copied onto each booking."""
    tutor, availability = make_tutor_with_schedule(client)
    t = _type(client, "Consultaton")           # typo
    link = _link(client, availability, "consult", t["id"])
    booking = _book(client, tutor, link)

    client.put(f"/booking_types/{t['id']}", json={"label": "Consultation", "color": "#6366f1"})

    # The booking's pointer is unchanged, but what it resolves to is corrected.
    assert client.get(f"/bookings/{booking['id']}").json()["booking_type_id"] == t["id"]
    assert client.get("/booking_types/").json()[0]["label"] == "Consultation"


def test_changing_a_links_type_leaves_existing_bookings_alone(client):
    """The other half: repointing the link reaches future generations only."""
    tutor, availability = make_tutor_with_schedule(client)
    old, new = _type(client, "Intro Consult"), _type(client, "Cheer Practice", "#f59e0b")
    link = _link(client, availability, "consult", old["id"])
    before = _book(client, tutor, link)

    client.put(f"/booking_links/{link['id']}", json={
        **booking_link_standalone, "slug": "consult", "booking_type_id": new["id"],
        "availability": availability,
    })
    after = _book(client, tutor, link, start="2099-06-17T16:00:00", end="2099-06-17T17:30:00")

    assert client.get(f"/bookings/{before['id']}").json()["booking_type_id"] == old["id"]
    assert client.get(f"/bookings/{after['id']}").json()["booking_type_id"] == new["id"]


def test_two_links_can_share_one_type(client):
    """Non-uniqueness across links is the feature — it's what makes Type differ from Source."""
    tutor, availability = make_tutor_with_schedule(client)
    shared = _type(client, "Tutoring")
    a = _link(client, availability, "weekly", shared["id"])
    b = _link(client, availability, "one-off", shared["id"])
    _book(client, tutor, a)
    _book(client, tutor, b, start="2099-06-17T16:00:00", end="2099-06-17T17:30:00")

    body = client.get(f"/bookings/?booking_type_ids={shared['id']}&time_min=2099-01-01T00:00:00Z&time_max=2100-01-01T00:00:00Z").json()
    assert len(body["items"]) == 2                          # one bucket under Type
    assert len({i["booking_link_id"] for i in body["items"]}) == 2   # two under Source


# ── STAMPING ────────────────────────────────────────────────────────────────

def test_series_occurrence_takes_its_type_from_the_series_not_the_link(client):
    """_ensure_occurrence copies off the series, so a link edited since can't reach into an
    existing series' future occurrences."""
    tutor, availability = make_tutor_with_schedule(client)
    original, changed = _type(client, "Tutoring"), _type(client, "Exam Prep", "#f59e0b")
    link = _link(client, availability, "weekly", original["id"], recurring=True)
    first = _book(client, tutor, link)

    client.put(f"/booking_links/{link['id']}", json={
        **booking_link_recurring, "slug": "weekly", "booking_type_id": changed["id"],
        "availability": availability,
    })

    # Materialize occurrence 2 by acting on it; it must inherit the series' type, not the link's.
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.delete(f"/bookings/{_next_occurrence_ref(first)}")

    db = TestingSessionLocal()
    try:
        assert db.query(BookingSeries).one().booking_type_id == original["id"]
        assert {b.booking_type_id for b in db.query(Booking).all()} == {original["id"]}
    finally:
        db.close()


def test_reschedule_carries_the_type_forward(client):
    tutor, availability = make_tutor_with_schedule(client)
    t = _type(client, "Tutoring")
    link = _link(client, availability, "one-off", t["id"])
    booking = _book(client, tutor, link)

    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        moved = client.post(f"/bookings/{booking['id']}/reschedule", json={
            "tutor_id": tutor["id"], "start": "2099-06-17T16:00:00",
            "end": "2099-06-17T17:30:00", "timezone": "America/New_York",
        }).json()
    assert moved["booking_type_id"] == t["id"]


# ── DELETION ────────────────────────────────────────────────────────────────

def test_deleting_a_type_nulls_referrers_without_deleting_them(client):
    """Unguarded at any usage count — a type carries no rules, so rows just lose their label."""
    tutor, availability = make_tutor_with_schedule(client)
    t = _type(client, "Tutoring")
    link = _link(client, availability, "one-off", t["id"])
    booking = _book(client, tutor, link)

    assert client.delete(f"/booking_types/{t['id']}").status_code == 204

    assert client.get(f"/bookings/{booking['id']}").json()["booking_type_id"] is None
    assert client.get(f"/booking_links/{link['id']}").json()["booking_type_id"] is None
    db = TestingSessionLocal()
    try:
        assert db.query(Booking).count() == 1          # the booking survives
        assert db.query(BookingLink).count() == 1      # so does the link
    finally:
        db.close()


def test_usage_counts_split_by_referrer(client):
    """Split because the losses differ: a booking loses a label off a historical record, a link
    just needs a new type picked."""
    tutor, availability = make_tutor_with_schedule(client)
    t = _type(client, "Tutoring")
    link = _link(client, availability, "weekly", t["id"], recurring=True)
    _book(client, tutor, link)

    usage = client.get(f"/booking_types/{t['id']}/usage").json()
    assert usage == {"links": 1, "bookings": 1, "series": 1}


def test_type_is_optional_everywhere(client):
    tutor, availability = make_tutor_with_schedule(client)
    link = _link(client, availability, "untyped", None)
    booking = _book(client, tutor, link)
    assert link["booking_type_id"] is None
    assert booking["booking_type_id"] is None


def test_relabelling_a_series_carries_to_all_its_occurrences(client):
    """The one place a booking's type changes without being edited directly — the admin picked the
    series, so the whole series is the scope."""
    tutor, availability = make_tutor_with_schedule(client)
    original, replacement = _type(client, "Tutoring"), _type(client, "Exam Prep", "#f59e0b")
    link = _link(client, availability, "weekly", original["id"], recurring=True)
    first = _book(client, tutor, link)

    # Materialize a second occurrence so there's more than one row to carry the change to.
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        client.delete(f"/bookings/{_next_occurrence_ref(first)}")

    client.put(f"/bookings/booking-series/{first['series_id']}", json={
        "cancel_mode": "auto",
        "reschedule_mode": "auto",
        "series_cancel_mode": "auto",
        "series_reschedule_mode": "auto",
        "booking_link_id": link["id"],
        "booking_type_id": replacement["id"],
    })

    db = TestingSessionLocal()
    try:
        assert db.query(BookingSeries).one().booking_type_id == replacement["id"]
        assert {b.booking_type_id for b in db.query(Booking).all()} == {replacement["id"]}
    finally:
        db.close()


def test_virtual_occurrences_carry_the_series_type(client):
    """A virtual occurrence is built in memory off the series, so it has to be labelled the same as
    a materialized one — otherwise one list shows the type on some rows of a series and not others."""
    tutor, availability = make_tutor_with_schedule(client)
    t = _type(client, "Tutoring")
    link = _link(client, availability, "weekly", t["id"], recurring=True)
    _book(client, tutor, link)

    items = client.get("/bookings/?time_min=2099-01-01T00:00:00Z&time_max=2099-12-31T00:00:00Z").json()["items"]
    assert len(items) > 1, "expected the first occurrence plus virtual ones"
    assert {i["booking_type_id"] for i in items} == {t["id"]}


def test_virtual_and_materialized_occurrences_agree_field_for_field(client):
    """Guards the two parallel constructions: _virtual_occurrences builds a BookingResponse in
    memory, _ensure_occurrence builds the real row. Both copy off the series field by field, so a
    new column added to Booking has to be added to both — miss one and the value silently differs
    depending on whether the occurrence happens to be materialized yet.
    """
    tutor, availability = make_tutor_with_schedule(client)
    t = _type(client, "Tutoring")
    link = _link(client, availability, "weekly", t["id"], recurring=True)
    first = _book(client, tutor, link)
    window = "time_min=2099-01-01T00:00:00Z&time_max=2099-12-31T00:00:00Z"

    # Occurrence 2 is virtual right now.
    ref = _next_occurrence_ref(first)
    before = next(i for i in client.get(f"/bookings/?{window}").json()["items"] if i["id"] == ref)

    # Materialize it via a contact-info PUT — a write that changes nothing else about the row.
    client.put(f"/bookings/{ref}", json={
        "cancel_mode": "auto",
        "reschedule_mode": "auto",
        "booking_link_id": link["id"],
        "booking_type_id": t["id"],
    })
    after = next(i for i in client.get(f"/bookings/?{window}").json()["items"] if i["id"] == ref)

    # start/end differ only by a `Z` suffix under SQLite, which drops tzinfo on read (Postgres
    # doesn't), so compare those as instants. `timezone` genuinely disagrees — virtual reports the
    # business zone, materialized the booker's — but that column is a request-time conversion input
    # on its way out, so it isn't worth reconciling.
    ignore = {"start", "end", "timezone"}
    assert {k: v for k, v in before.items() if k not in ignore} == \
           {k: v for k, v in after.items() if k not in ignore}, (
        "a virtual occurrence and its materialized twin disagree — a field is probably set in "
        "_ensure_occurrence but missing from _virtual_occurrences, or vice versa"
    )
    for field in ("start", "end"):
        virtual = datetime.fromisoformat(before[field].replace("Z", "+00:00"))
        materialized = datetime.fromisoformat(after[field].replace("Z", "+00:00"))
        assert virtual == materialized.replace(tzinfo=materialized.tzinfo or UTC)


# ── FACETS ──────────────────────────────────────────────────────────────────

def _two_typed_bookings(client):
    """Two bookings under different links and different types, so each facet dimension has two
    options to narrow (or not narrow) independently."""
    tutor, availability = make_tutor_with_schedule(client)
    tutoring, consult = _type(client, "Tutoring"), _type(client, "Consultation", "#0ea5e9")
    link_a = _link(client, availability, "weekly", tutoring["id"])
    link_b = _link(client, availability, "intro", consult["id"])
    _book(client, tutor, link_a,
          payer={"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com", "phone": "555-0001"})
    _book(client, tutor, link_b,
          payer={"first_name": "Bob", "last_name": "Jones", "email": "bob@example.com", "phone": "555-0002"},
          start="2099-06-11T16:00:00", end="2099-06-11T17:00:00")
    return tutoring, consult, link_a, link_b


def test_booking_type_facet_self_excludes(client):
    """Selecting a type must not remove the others from its own facet — otherwise picking one
    collapses the list and you can never switch. Other dimensions do narrow."""
    tutoring, consult, link_a, _ = _two_typed_bookings(client)

    body = client.get(f"/bookings/?booking_type_ids={tutoring['id']}&{WINDOW}").json()

    assert {t["id"] for t in body["facets"]["booking_types"]} == {tutoring["id"], consult["id"]}
    assert {l["id"] for l in body["facets"]["booking_links"]} == {link_a["id"]}
    assert {(a["first_name"], a["last_name"]) for a in body["facets"]["attendees"]} == {("Alice", "Smith")}


def test_booking_type_facet_narrows_under_another_filter(client):
    """The converse: filtering by link *does* narrow the type options, since that isn't its own."""
    tutoring, _, link_a, _ = _two_typed_bookings(client)

    body = client.get(f"/bookings/?booking_link_ids={link_a['id']}&{WINDOW}").json()
    assert {t["id"] for t in body["facets"]["booking_types"]} == {tutoring["id"]}


def test_series_booking_type_facet_self_excludes(client):
    """Same rule on the series endpoint, which computes facets through its own path."""
    tutor, availability = make_tutor_with_schedule(client)
    tutoring, consult = _type(client, "Tutoring"), _type(client, "Consultation", "#0ea5e9")
    link_a = _link(client, availability, "weekly", tutoring["id"], recurring=True)
    link_b = _link(client, availability, "biweekly", consult["id"], recurring=True)
    _book(client, tutor, link_a)
    _book(client, tutor, link_b, start="2099-06-11T16:00:00", end="2099-06-11T17:00:00")

    body = client.get(f"/bookings/booking-series?booking_type_ids={tutoring['id']}").json()

    assert {t["id"] for t in body["facets"]["booking_types"]} == {tutoring["id"], consult["id"]}
    assert {l["id"] for l in body["facets"]["booking_links"]} == {link_a["id"]}


def test_reclassifying_one_booking_moves_only_that_row(client):
    """Counterpart to the series cascade: a standalone booking's type is its own."""
    tutor, availability = make_tutor_with_schedule(client)
    tutoring, consult = _type(client, "Tutoring"), _type(client, "Consultation", "#0ea5e9")
    link = _link(client, availability, "weekly", tutoring["id"])
    first = _book(client, tutor, link)
    second = _book(client, tutor, link, start="2099-06-11T16:00:00", end="2099-06-11T17:00:00")

    client.put(f"/bookings/{first['id']}", json={
        "cancel_mode": "auto",
        "reschedule_mode": "auto",
        "booking_link_id": link["id"],
        "booking_type_id": consult["id"],
    })

    assert client.get(f"/bookings/{first['id']}").json()["booking_type_id"] == consult["id"]
    assert client.get(f"/bookings/{second['id']}").json()["booking_type_id"] == tutoring["id"]
    assert client.get(f"/booking_links/{link['id']}").json()["booking_type_id"] == tutoring["id"]
