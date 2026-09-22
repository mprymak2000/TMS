"""Enrollment is an extension of a contact, not a thing they have — its id IS the contact's id.

Name, email and phone live on the contact and are tested in test_contacts.py. What's here is the
rate, the dates, and the upsert semantics that fall out of the shared key.
"""
import pytest

from conftest import TestingSessionLocal
from models import ContactManager


def _contact(client, first="Student", last="A", email=None):
    return client.post("/contacts/", json={
        "first_name": first,
        "last_name": last,
        "email": email or f"{first}.{last}@example.com".lower(),
    }).json()


enrollment_required = {
    "rate": 55,
    "start_date": "2021-04-01",
}

#will be updated in every mutable field
enrollment_wrong = {
    "rate": 1,
    "start_date": "1999-01-01",
    "is_active": False,
    "grade": 150,
    "birthday": "2009-01-01",
}

enrollment_correct = {
    "rate": 65,
    "start_date": "2022-10-01",
    "is_active": True,
    "grade": 11,
    "birthday": "2009-01-01",
}


@pytest.fixture
def enrolled(client):
    """A contact plus their enrollment, in the state enrollment_wrong describes."""
    contact = _contact(client, last="C")
    created = client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_wrong).json()
    return contact, created


# --- CREATE (via upsert) ---

def test_enroll_required_fields(client):
    contact = _contact(client)
    response = client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_required)
    assert response.status_code == 201
    data = response.json()
    assert data["rate"] == enrollment_required["rate"]
    assert data["start_date"] == enrollment_required["start_date"]
    assert data["is_active"]  # true is default value
    assert data["grade"] is None
    assert data["birthday"] is None


# The shared key is the whole point: an enrollment has no identity of its own to look up.
def test_enrollment_id_is_the_contact_id(client):
    contact = _contact(client)
    created = client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_required).json()
    assert created["id"] == contact["id"]


def test_enroll_all_fields(client):
    contact = _contact(client, last="C")
    response = client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_wrong)
    assert response.status_code == 201
    data = response.json()
    assert not data["is_active"]
    assert data["birthday"] == enrollment_wrong["birthday"]
    assert data["grade"] == enrollment_wrong["grade"]


def test_enroll_missing_required_fields(client):
    contact = _contact(client)
    assert client.put(f"/contacts/{contact['id']}/enrollment", json={}).status_code == 422


def test_enroll_unknown_contact(client):
    assert client.put("/contacts/9999/enrollment", json=enrollment_required).status_code == 404


def test_enroll_invalid_rate(client):
    contact = _contact(client)
    response = client.put(f"/contacts/{contact['id']}/enrollment", json={**enrollment_required, "rate": -10})
    assert response.status_code == 422


def test_enroll_invalid_date(client):
    contact = _contact(client)
    response = client.put(f"/contacts/{contact['id']}/enrollment", json={**enrollment_required, "start_date": "not-a-date"})
    assert response.status_code == 422


# --- UPSERT ---

# There can only ever be one, so the second call replaces rather than conflicting. That's what lets
# the client save without first knowing whether this person is enrolled.
def test_second_put_replaces_rather_than_conflicting(client):
    contact = _contact(client)
    assert client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_wrong).status_code == 201

    response = client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_correct)
    assert response.status_code == 200
    assert response.json()["rate"] == enrollment_correct["rate"]

    # Still one row, reachable from the contact.
    row = client.get(f"/contacts/?enrolled=true").json()["items"][0]
    assert row["enrollment"]["rate"] == enrollment_correct["rate"]


def test_put_is_idempotent(client):
    contact = _contact(client)
    client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_correct)
    first = client.get("/contacts/?enrolled=true").json()["items"][0]["enrollment"]
    client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_correct)
    second = client.get("/contacts/?enrolled=true").json()["items"][0]["enrollment"]
    assert first == second


def test_update_enrollment(client, enrolled):
    contact, _created = enrolled
    response = client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_correct)
    assert response.status_code == 200
    data = response.json()
    assert data["rate"] == enrollment_correct["rate"]
    assert data["start_date"] == enrollment_correct["start_date"]
    assert data["is_active"] == enrollment_correct["is_active"]
    assert data["grade"] == enrollment_correct["grade"]


# --- COMBINED WRITE, through the contact PUT ---

# The client panel saves identity and rate with one button. Two requests could leave a renamed
# client on the old rate if the second failed, so the contact PUT carries the enrollment too.
def test_contact_put_can_carry_the_enrollment(client):
    contact = _contact(client, first="Marcus", last="Chen")
    response = client.put(f"/contacts/{contact['id']}", json={
        "first_name": "Marcus", "last_name": "Chen-Alvarez", "email": contact["email"],
        "enrollment": enrollment_required,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["last_name"] == "Chen-Alvarez"
    assert body["enrollment"]["rate"] == enrollment_required["rate"]


def test_contact_put_without_enrollment_leaves_it_alone(client):
    contact = _contact(client)
    client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_correct)

    response = client.put(f"/contacts/{contact['id']}", json={
        "first_name": "Renamed", "last_name": "Person", "email": contact["email"],
    })
    assert response.status_code == 200
    assert response.json()["enrollment"]["rate"] == enrollment_correct["rate"]


# Atomic: a bad email on the identity side must not leave a new enrollment behind.
def test_contact_put_rolls_back_enrollment_when_identity_fails(client):
    other = _contact(client, first="Taken", last="Email")
    contact = _contact(client)
    response = client.put(f"/contacts/{contact['id']}", json={
        "first_name": "X", "last_name": "Y", "email": other["email"],
        "enrollment": enrollment_required,
    })
    assert response.status_code == 409
    row = next(c for c in client.get("/contacts/").json()["items"] if c["id"] == contact["id"])
    assert row["enrollment"] is None


# --- READ, through the contact ---

def test_enrollment_is_nested_on_the_contact(client):
    contact = _contact(client)
    client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_required)
    row = next(c for c in client.get("/contacts/").json()["items"] if c["id"] == contact["id"])
    assert row["enrollment"]["rate"] == enrollment_required["rate"]


def test_unenrolled_contact_has_null_enrollment(client):
    contact = _contact(client)
    row = next(c for c in client.get("/contacts/").json()["items"] if c["id"] == contact["id"])
    assert row["enrollment"] is None


# --- DELETE ---

# For mistakes only. Someone who stopped coming gets is_active=False, which keeps their rate,
# start date and grade; this throws all of it away.
def test_delete_enrollment_leaves_the_contact(client):
    contact = _contact(client, first="ToDelete", last="Lastname")
    client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_wrong)

    assert client.delete(f"/contacts/{contact['id']}/enrollment").status_code == 200
    assert client.get(f"/contacts/{contact['id']}").status_code == 200
    row = next(c for c in client.get("/contacts/").json()["items"] if c["id"] == contact["id"])
    assert row["enrollment"] is None


def test_delete_enrollment_not_enrolled(client):
    contact = _contact(client)
    assert client.delete(f"/contacts/{contact['id']}/enrollment").status_code == 404


def test_delete_enrollment_with_lessons_conflicts(client, setup):
    enrollment, _tutor, lesson = setup
    client.post("/lessons/", json=lesson)
    assert client.delete(f"/contacts/{enrollment['id']}/enrollment").status_code == 409


# The enrollment is part of the contact, so it goes when they do — no separate guard needed.
def test_deleting_the_contact_cascades_the_enrollment(client):
    contact = _contact(client)
    client.put(f"/contacts/{contact['id']}/enrollment", json=enrollment_required)

    assert client.delete(f"/contacts/{contact['id']}").status_code == 200
    assert client.get("/contacts/?enrolled=true").json()["total"] == 0


# --- FILTERS ---

def test_enrolled_filter_only_returns_the_enrolled(client):
    enrolled_contact = _contact(client, first="Enrolled", last="One")
    _contact(client, first="Plain", last="Two")
    client.put(f"/contacts/{enrolled_contact['id']}/enrollment", json=enrollment_required)

    body = client.get("/contacts/?enrolled=true").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == enrolled_contact["id"]
    assert client.get("/contacts/").json()["total"] == 2


# Someone who left still has history worth finding, so the filter is "has an enrollment", not
# "is currently enrolled".
def test_enrolled_filter_includes_inactive(client):
    contact = _contact(client)
    client.put(f"/contacts/{contact['id']}/enrollment", json={**enrollment_required, "is_active": False})
    assert client.get("/contacts/?enrolled=true").json()["total"] == 1


def test_enrolled_filter_composes_with_search(client):
    a = _contact(client, first="Marcus", last="Chen")
    b = _contact(client, first="Sofia", last="Chen")
    client.put(f"/contacts/{a['id']}/enrollment", json=enrollment_required)
    client.put(f"/contacts/{b['id']}/enrollment", json=enrollment_required)

    assert client.get("/contacts/?enrolled=true&search=chen").json()["total"] == 2
    assert client.get("/contacts/?enrolled=true&search=marcus").json()["total"] == 1


def test_manages_filter_returns_managers_only(client):
    payer = _contact(client, first="Rita", last="Alvarez")
    dependent = _contact(client, first="Marcus", last="Chen")
    _contact(client, first="Plain", last="Person")
    with TestingSessionLocal() as db:
        db.add(ContactManager(manager_id=payer["id"], managed_id=dependent["id"]))
        db.commit()

    body = client.get("/contacts/?manages=true").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == payer["id"]


# --- RELATIONSHIPS ---

def test_relationships_both_directions(client):
    payer = _contact(client, first="Rita", last="Alvarez")
    kid_a = _contact(client, first="Marcus", last="Chen")
    kid_b = _contact(client, first="Ana", last="Chen")
    _contact(client, first="Plain", last="Person")
    with TestingSessionLocal() as db:
        db.add(ContactManager(manager_id=payer["id"], managed_id=kid_a["id"]))
        db.add(ContactManager(manager_id=payer["id"], managed_id=kid_b["id"]))
        db.commit()

    body = client.get(f"/contacts/{payer['id']}/relationships").json()
    assert [c["first_name"] for c in body["manages"]] == ["Ana", "Marcus"]
    assert body["managed_by"] == []

    body = client.get(f"/contacts/{kid_a['id']}/relationships").json()
    assert body["manages"] == []
    assert [c["id"] for c in body["managed_by"]] == [payer["id"]]


def test_relationships_unknown_contact(client):
    assert client.get("/contacts/9999/relationships").status_code == 404


# The two are independent booleans, not one enum, precisely so they can be AND'd: an adult who pays
# for a child and is enrolled themselves is in both sets, and only the intersection finds them.
def test_filters_combine(client):
    both = _contact(client, first="Both", last="Roles")
    only_enrolled = _contact(client, first="Only", last="Enrolled")
    only_manages = _contact(client, first="Only", last="Manages")
    dependent = _contact(client, first="Some", last="Kid")
    client.put(f"/contacts/{both['id']}/enrollment", json=enrollment_required)
    client.put(f"/contacts/{only_enrolled['id']}/enrollment", json=enrollment_required)
    with TestingSessionLocal() as db:
        db.add(ContactManager(manager_id=both["id"], managed_id=dependent["id"]))
        db.add(ContactManager(manager_id=only_manages["id"], managed_id=dependent["id"]))
        db.commit()

    assert client.get("/contacts/?enrolled=true").json()["total"] == 2
    assert client.get("/contacts/?manages=true").json()["total"] == 2
    intersection = client.get("/contacts/?enrolled=true&manages=true").json()
    assert intersection["total"] == 1
    assert intersection["items"][0]["id"] == both["id"]
