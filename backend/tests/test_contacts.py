"""Contact identity: the resolver branches, the CRUD, and the delete guards.

The resolver is exercised through POST /bookings/ rather than called directly, so these cover the
real path including the relationship link. Every branch in resolve_attendee's docstring has a test
here, including the ones that deliberately duplicate.
"""
from unittest.mock import patch

from conftest import TestingSessionLocal
from models import Contact, ContactManager

from tests.test_bookings import (
    booking_payload,
    mock_calendar_service,
    setup_standalone,
    tutor_payload,
    _schedule,
    booking_link_standalone,
)


# ── helpers ──────────────────────────────────────────────────────────────────

SLOT_B = {"start": "2099-06-11T16:00:00", "end": "2099-06-11T17:00:00"}
SLOT_C = {"start": "2099-06-12T16:00:00", "end": "2099-06-12T17:00:00"}

PAYER = {"first_name": "Dana", "last_name": "Ruiz", "email": "dana@example.com", "phone": "555-0101"}


def _book(client, tutor, link, *, payer=None, attendee=None, **overrides):
    payload = {
        **booking_payload,
        "tutor_id": tutor["id"],
        "booking_link_id": link["id"],
        "payer": payer or PAYER,
        **overrides,
    }
    if attendee is not None:
        payload["attendee"] = attendee
    with patch("routers.bookings.get_calendar_service", return_value=mock_calendar_service()):
        return client.post("/bookings/", json=payload)


def _contacts():
    with TestingSessionLocal() as db:
        return db.query(Contact).order_by(Contact.id).all()


def _links():
    with TestingSessionLocal() as db:
        return db.query(ContactManager).order_by(ContactManager.id).all()


# ── self-booking ─────────────────────────────────────────────────────────────

def test_omitted_attendee_books_for_self(client):
    """Both FKs land on one contact, and nobody manages themselves."""
    tutor, link = setup_standalone(client)
    booking = _book(client, tutor, link).json()

    assert booking["payer"]["id"] == booking["attendee"]["id"]
    assert len(_contacts()) == 1
    assert _links() == []


def test_attendee_restating_payer_email_is_rejected(client):
    """Self-booking is expressed by omitting the attendee, not by repeating the payer."""
    tutor, link = setup_standalone(client)
    response = _book(client, tutor, link, attendee={**PAYER})
    assert response.status_code == 422


def test_attendee_restating_payer_name_without_email_is_rejected(client):
    """Same name and no email to tell them apart. A different email would be a legitimate Jr."""
    tutor, link = setup_standalone(client)
    response = _book(client, tutor, link, attendee={"first_name": "Dana", "last_name": "Ruiz"})
    assert response.status_code == 422


def test_same_name_different_email_is_allowed(client):
    """Jr./Sr. — the email is what distinguishes them."""
    tutor, link = setup_standalone(client)
    response = _book(client, tutor, link,
                     attendee={"first_name": "Dana", "last_name": "Ruiz", "email": "dana.jr@example.com"})
    assert response.status_code == 201
    booking = response.json()
    assert booking["payer"]["id"] != booking["attendee"]["id"]


# ── attendee with no email ───────────────────────────────────────────────────

def test_attendee_without_email_creates_contact_and_link(client):
    tutor, link = setup_standalone(client)
    booking = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}).json()

    assert booking["attendee"]["email"] is None
    assert booking["payer"]["id"] != booking["attendee"]["id"]
    assert len(_contacts()) == 2

    links = _links()
    assert len(links) == 1
    assert (links[0].manager_id, links[0].managed_id) == (booking["payer"]["id"], booking["attendee"]["id"])


def test_repeat_booking_for_same_emailless_attendee_reuses_the_row(client):
    """The name match, scoped to this payer. Without it a parent booking twice gets two Leos."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}).json()
    second = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}, **SLOT_B).json()

    assert first["attendee"]["id"] == second["attendee"]["id"]
    assert len(_contacts()) == 2
    assert len(_links()) == 1  # idempotent, no second link row


def test_name_match_is_case_insensitive(client):
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}).json()
    second = _book(client, tutor, link, attendee={"first_name": "leo", "last_name": "RUIZ"}, **SLOT_B).json()
    assert first["attendee"]["id"] == second["attendee"]["id"]


def test_second_payer_booking_same_emailless_attendee_duplicates(client):
    """DUPE, accepted: the name scope is per-payer, so nothing links the two rows."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}).json()
    other_payer = {"first_name": "Sam", "last_name": "Ruiz", "email": "sam@example.com", "phone": "555-0202"}
    second = _book(client, tutor, link, payer=other_payer,
                   attendee={"first_name": "Leo", "last_name": "Ruiz"}, **SLOT_B).json()

    assert first["attendee"]["id"] != second["attendee"]["id"]
    assert len(_contacts()) == 4  # Dana, Leo, Sam, Leo#2


# ── attendee with an email ───────────────────────────────────────────────────

def test_attendee_email_backfills_onto_an_emailless_match(client):
    """A dependent first booked without an address gets keyed properly from then on."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}).json()
    assert first["attendee"]["email"] is None

    second = _book(client, tutor, link,
                   attendee={"first_name": "Leo", "last_name": "Ruiz", "email": "leo@example.com"},
                   **SLOT_B).json()

    assert second["attendee"]["id"] == first["attendee"]["id"]
    assert second["attendee"]["email"] == "leo@example.com"
    assert len(_contacts()) == 2


def test_name_match_with_a_different_email_duplicates(client):
    """DUPE, deliberate: never merge on a name when the addresses disagree."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link,
                  attendee={"first_name": "Leo", "last_name": "Ruiz", "email": "leo@example.com"}).json()
    second = _book(client, tutor, link,
                   attendee={"first_name": "Leo", "last_name": "Ruiz", "email": "leo.new@example.com"},
                   **SLOT_B).json()

    assert first["attendee"]["id"] != second["attendee"]["id"]
    assert len(_contacts()) == 3  # Dana, Leo, Leo#2


def test_no_email_reuses_a_match_that_has_one(client):
    """The email clause only applies when an email is supplied, so a name-only booking still finds
    a dependent who already has an address."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link,
                  attendee={"first_name": "Leo", "last_name": "Ruiz", "email": "leo@example.com"}).json()
    second = _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"}, **SLOT_B).json()

    assert second["attendee"]["id"] == first["attendee"]["id"]
    assert second["attendee"]["email"] == "leo@example.com"  # preserved


def test_attendee_email_matches_globally_across_payers(client):
    """Not payer-scoped: an address belongs to one person, so a second payer reuses the row and
    simply gains a link. Two payers, one dependent."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link,
                  attendee={"first_name": "Leo", "last_name": "Ruiz", "email": "leo@example.com"}).json()
    other_payer = {"first_name": "Sam", "last_name": "Ruiz", "email": "sam@example.com", "phone": "555-0202"}
    second = _book(client, tutor, link, payer=other_payer,
                   attendee={"first_name": "Leo", "last_name": "Ruiz", "email": "leo@example.com"},
                   **SLOT_B).json()

    assert first["attendee"]["id"] == second["attendee"]["id"]
    assert len(_links()) == 2  # Dana→Leo and Sam→Leo
    assert {l.manager_id for l in _links()} == {first["payer"]["id"], second["payer"]["id"]}


# ── payer resolution ─────────────────────────────────────────────────────────

def test_payer_is_reused_by_email(client):
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link).json()
    second = _book(client, tutor, link, **SLOT_B).json()

    assert first["payer"]["id"] == second["payer"]["id"]
    assert len(_contacts()) == 1


def test_payer_email_is_normalized(client):
    """Stored lowercased and stripped, so casing doesn't fork a contact."""
    tutor, link = setup_standalone(client)
    first = _book(client, tutor, link, payer={**PAYER, "email": "  Dana@Example.COM "}).json()
    assert first["payer"]["email"] == "dana@example.com"

    second = _book(client, tutor, link, **SLOT_B).json()
    assert second["payer"]["id"] == first["payer"]["id"]


def test_payer_profile_refreshes_while_unclaimed(client):
    """Nobody has proven they own the address, so the newest booking wins."""
    tutor, link = setup_standalone(client)
    _book(client, tutor, link)
    second = _book(client, tutor, link,
                   payer={**PAYER, "first_name": "Danielle", "phone": "555-9999"}, **SLOT_B).json()

    assert second["payer"]["first_name"] == "Danielle"
    assert second["payer"]["phone"] == "555-9999"
    assert len(_contacts()) == 1


def test_payer_profile_is_protected_once_verified(client):
    """A public form can't rewrite a record whose owner proved the inbox."""
    tutor, link = setup_standalone(client)
    created = _book(client, tutor, link).json()

    with TestingSessionLocal() as db:
        from datetime import UTC, datetime
        row = db.query(Contact).filter(Contact.id == created["payer"]["id"]).first()
        row.verified_at = datetime.now(UTC)
        db.commit()

    second = _book(client, tutor, link,
                   payer={**PAYER, "first_name": "Impostor", "phone": "555-6666"}, **SLOT_B).json()

    assert second["payer"]["id"] == created["payer"]["id"]  # still attaches
    assert second["payer"]["first_name"] == "Dana"           # but doesn't overwrite
    assert second["payer"]["phone"] == "555-0101"


# ── guest reminder phone ─────────────────────────────────────────────────────

def test_guest_booking_freezes_the_reminder_phone(client):
    tutor, link = setup_standalone(client)
    booking = _book(client, tutor, link).json()
    assert booking["guest_reminder_phone"] == "555-0101"


def test_verified_payer_leaves_reminder_phone_null(client):
    """Send time reads contact.phone live instead, so a profile edit reaches every upcoming booking."""
    tutor, link = setup_standalone(client)
    created = _book(client, tutor, link).json()

    with TestingSessionLocal() as db:
        from datetime import UTC, datetime
        row = db.query(Contact).filter(Contact.id == created["payer"]["id"]).first()
        row.verified_at = datetime.now(UTC)
        db.commit()

    second = _book(client, tutor, link, **SLOT_B).json()
    assert second["guest_reminder_phone"] is None


# ── CRUD ─────────────────────────────────────────────────────────────────────

def test_create_and_get_contact(client):
    created = client.post("/contacts/", json={"first_name": "Dana", "last_name": "Ruiz", "email": "Dana@Example.com"}).json()
    assert created["email"] == "dana@example.com"
    assert client.get(f"/contacts/{created['id']}").status_code == 200


def test_create_contact_duplicate_email_conflicts(client):
    client.post("/contacts/", json={"first_name": "Dana", "last_name": "Ruiz", "email": "dana@example.com"})
    response = client.post("/contacts/", json={"first_name": "Other", "last_name": "Person", "email": "DANA@example.com"})
    assert response.status_code == 409


def test_contacts_without_email_do_not_collide(client):
    """NULL isn't a value, so any number of emailless dependents can coexist."""
    client.post("/contacts/", json={"first_name": "Leo", "last_name": "Ruiz"})
    response = client.post("/contacts/", json={"first_name": "Mia", "last_name": "Ruiz"})
    assert response.status_code == 201


def test_update_contact_reaches_existing_bookings(client):
    """No snapshot on the booking, so a correction applies to their whole history."""
    tutor, link = setup_standalone(client)
    booking = _book(client, tutor, link).json()

    client.put(f"/contacts/{booking['payer']['id']}", json={
        "first_name": "Dana", "last_name": "Ruiz-Ortega", "email": "dana@example.com", "phone": "555-1111",
    })

    refetched = client.get(f"/bookings/{booking['id']}").json()
    assert refetched["payer"]["last_name"] == "Ruiz-Ortega"
    assert refetched["payer"]["phone"] == "555-1111"


def test_update_contact_keeping_own_email_is_not_a_conflict(client):
    created = client.post("/contacts/", json={"first_name": "Dana", "last_name": "Ruiz", "email": "dana@example.com"}).json()
    response = client.put(f"/contacts/{created['id']}", json={
        "first_name": "Dana", "last_name": "Ruiz", "email": "dana@example.com", "phone": "555-0101",
    })
    assert response.status_code == 200


def test_update_contact_taking_someone_elses_email_conflicts(client):
    client.post("/contacts/", json={"first_name": "Sam", "last_name": "Ruiz", "email": "sam@example.com"})
    created = client.post("/contacts/", json={"first_name": "Dana", "last_name": "Ruiz", "email": "dana@example.com"}).json()
    response = client.put(f"/contacts/{created['id']}", json={
        "first_name": "Dana", "last_name": "Ruiz", "email": "sam@example.com",
    })
    assert response.status_code == 409


def test_contact_search_matches_name_and_email(client):
    client.post("/contacts/", json={"first_name": "Dana", "last_name": "Ruiz", "email": "dana@example.com"})
    client.post("/contacts/", json={"first_name": "Sam", "last_name": "Jones", "email": "sam@example.com"})

    assert client.get("/contacts/?search=ruiz").json()["total"] == 1
    assert client.get("/contacts/?search=SAM@").json()["total"] == 1
    assert len(client.get("/contacts/").json()["items"]) == 2


# Neither column holds "dana ruiz" on its own, so this only matches if the joined name is searched.
def test_contact_search_matches_a_full_name(client):
    client.post("/contacts/", json={"first_name": "Dana", "last_name": "Ruiz"})
    client.post("/contacts/", json={"first_name": "Sam", "last_name": "Jones"})

    assert client.get("/contacts/?search=dana%20ruiz").json()["total"] == 1
    assert client.get("/contacts/?search=DANA%20Ruiz").json()["total"] == 1
    assert client.get("/contacts/?search=dana%20jones").json()["total"] == 0


# ── roster listing: paging, sorting, role counts ─────────────────────────────

# total counts every match, not just the rows on this page — that's the whole reason the roster
# pages by number rather than by cursor.
def test_contact_list_pages_with_a_full_total(client):
    for i in range(5):
        client.post("/contacts/", json={"first_name": f"Person{i}", "last_name": "Test"})

    body = client.get("/contacts/?page=1&page_size=2").json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    assert len(client.get("/contacts/?page=3&page_size=2").json()["items"]) == 1
    assert client.get("/contacts/?page=9&page_size=2").json()["items"] == []


def test_contact_list_sorts_by_name_both_directions(client):
    client.post("/contacts/", json={"first_name": "Zoe", "last_name": "Alvarez"})
    client.post("/contacts/", json={"first_name": "Adam", "last_name": "Young"})

    assert client.get("/contacts/").json()["items"][0]["first_name"] == "Adam"
    assert client.get("/contacts/?direction=desc").json()["items"][0]["first_name"] == "Zoe"


def test_contact_list_rejects_an_unknown_sort(client):
    assert client.get("/contacts/?sort=rate").status_code == 422
    assert client.get("/contacts/?direction=sideways").status_code == 422


# Roles are derived from the bookings, never stored, and one person can hold both at once.
def test_contact_list_reports_role_counts(client):
    tutor, link = setup_standalone(client)
    booking = _book(client, tutor, link).json()
    payer_id = booking["payer"]["id"]
    attendee_id = booking["attendee"]["id"]

    rows = {c["id"]: c for c in client.get("/contacts/").json()["items"]}
    assert rows[payer_id]["bookings_as_payer"] == 1
    assert rows[attendee_id]["bookings_as_attendee"] == 1


def test_contact_list_reports_zero_for_someone_who_never_booked(client):
    created = client.post("/contacts/", json={"first_name": "Unbooked", "last_name": "Person"}).json()
    row = next(c for c in client.get("/contacts/").json()["items"] if c["id"] == created["id"])
    assert row["bookings_as_payer"] == 0
    assert row["bookings_as_attendee"] == 0


# ── delete guards ────────────────────────────────────────────────────────────

def test_delete_contact_with_bookings_conflicts(client):
    tutor, link = setup_standalone(client)
    booking = _book(client, tutor, link).json()
    assert client.delete(f"/contacts/{booking['payer']['id']}").status_code == 409


def test_delete_contact_with_enrollment_conflicts(client):
    created = client.post("/contacts/", json={"first_name": "Leo", "last_name": "Ruiz"}).json()
    client.post("/students/", json={"contact_id": created["id"], "rate": 60, "start_date": "2026-01-01"})
    assert client.delete(f"/contacts/{created['id']}").status_code == 409


def test_delete_unreferenced_contact_clears_its_links(client):
    """The stray-duplicate cleanup: once nothing points at them, the rows naming them go too.

    Deletes from the managed side, since a manager can't be deleted at all (below)."""
    tutor, link = setup_standalone(client)
    _book(client, tutor, link, attendee={"first_name": "Leo", "last_name": "Ruiz"})
    payer_id, attendee_id = _links()[0].manager_id, _links()[0].managed_id

    # A second dependent under the same payer, with no bookings of their own.
    stray = client.post("/contacts/", json={"first_name": "Stray", "last_name": "Dependent"}).json()
    with TestingSessionLocal() as db:
        db.add(ContactManager(manager_id=payer_id, managed_id=stray["id"]))
        db.commit()
    assert len(_links()) == 2

    assert client.delete(f"/contacts/{stray['id']}").status_code == 200
    assert [(l.manager_id, l.managed_id) for l in _links()] == [(payer_id, attendee_id)]


# A dependent left with no manager has nobody to book or bill for them — and the booking guards
# miss it, since a monthly enrollment bills on a schedule with no booking involved.
def test_delete_contact_who_manages_others_conflicts(client):
    # No bookings anywhere, so this is the manager guard firing rather than the booking one.
    payer = client.post("/contacts/", json={"first_name": "Rita", "last_name": "Alvarez"}).json()
    dependent = client.post("/contacts/", json={"first_name": "Marcus", "last_name": "Chen"}).json()
    with TestingSessionLocal() as db:
        db.add(ContactManager(manager_id=payer["id"], managed_id=dependent["id"]))
        db.commit()

    response = client.delete(f"/contacts/{payer['id']}")
    assert response.status_code == 409
    assert "manages others" in response.json()["detail"]


# The guard is about managing, not about being managed: a dependent is still deletable.
def test_delete_contact_who_is_managed_is_allowed(client):
    payer = client.post("/contacts/", json={"first_name": "Rita", "last_name": "Alvarez"}).json()
    dependent = client.post("/contacts/", json={"first_name": "Marcus", "last_name": "Chen"}).json()
    with TestingSessionLocal() as db:
        db.add(ContactManager(manager_id=payer["id"], managed_id=dependent["id"]))
        db.commit()

    assert client.delete(f"/contacts/{dependent['id']}").status_code == 200
    assert _links() == []
    # The manager survives, and is now deletable since they manage nobody.
    assert client.delete(f"/contacts/{payer['id']}").status_code == 200


def test_delete_contact_not_found(client):
    assert client.delete("/contacts/9999").status_code == 404
