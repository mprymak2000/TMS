"""Student is enrollment, not identity — a billing relationship on a Contact.

Name, email and phone live on the contact and are tested in test_contacts.py. What's here is the
rate, the dates, and the guards around enrolling someone.
"""
import pytest


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
    created = client.post("/students/", json={**enrollment_wrong, "contact_id": contact["id"]}).json()
    return contact, created


# --- CREATE ---

def test_create_student_required_fields(client):
    contact = _contact(client)
    response = client.post("/students/", json={**enrollment_required, "contact_id": contact["id"]})
    assert response.status_code == 201
    data = response.json()
    assert data["contact_id"] == contact["id"]
    assert data["contact"]["first_name"] == "Student"
    assert data["rate"] == enrollment_required["rate"]
    assert data["start_date"] == enrollment_required["start_date"]
    assert data["is_active"]  # true is default value
    assert data["grade"] is None
    assert data["birthday"] is None
    assert "id" in data


def test_create_student_all_fields(client):
    contact = _contact(client, last="C")
    response = client.post("/students/", json={**enrollment_wrong, "contact_id": contact["id"]})
    assert response.status_code == 201
    data = response.json()
    assert not data["is_active"]
    assert data["birthday"] == enrollment_wrong["birthday"]
    assert data["grade"] == enrollment_wrong["grade"]


def test_create_student_missing_required_fields(client):
    contact = _contact(client)
    response = client.post("/students/", json={"contact_id": contact["id"]})
    assert response.status_code == 422


def test_create_student_unknown_contact(client):
    response = client.post("/students/", json={**enrollment_required, "contact_id": 9999})
    assert response.status_code == 404


def test_create_student_contact_already_enrolled(client):
    """One enrollment per contact — the FK is unique, so a second is a conflict, not a second rate."""
    contact = _contact(client)
    client.post("/students/", json={**enrollment_required, "contact_id": contact["id"]})
    response = client.post("/students/", json={**enrollment_required, "contact_id": contact["id"]})
    assert response.status_code == 409


def test_create_student_invalid_rate(client):
    contact = _contact(client)
    response = client.post("/students/", json={**enrollment_required, "contact_id": contact["id"], "rate": -10})
    assert response.status_code == 422


def test_create_student_invalid_date(client):
    contact = _contact(client)
    response = client.post("/students/", json={**enrollment_required, "contact_id": contact["id"], "start_date": "not-a-date"})
    assert response.status_code == 422


# --- GET ---

def test_get_students(client):
    client.post("/students/", json={**enrollment_required, "contact_id": _contact(client, last="A")["id"]})
    client.post("/students/", json={**enrollment_required, "contact_id": _contact(client, last="B")["id"]})
    response = client.get("/students/")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_student_by_id(client):
    contact = _contact(client)
    created = client.post("/students/", json={**enrollment_required, "contact_id": contact["id"]}).json()
    response = client.get(f"/students/{created['id']}")
    assert response.status_code == 200
    assert response.json()["contact"]["first_name"] == "Student"


def test_get_student_by_id_not_found(client):
    response = client.get("/students/9999")
    assert response.status_code == 404


# --- UPDATE ---

def test_update_student(client, enrolled):
    _contact_row, created = enrolled
    response = client.put(f"/students/{created['id']}", json=enrollment_correct)
    assert response.status_code == 200
    data = response.json()
    assert data["rate"] == enrollment_correct["rate"]
    assert data["start_date"] == enrollment_correct["start_date"]
    assert data["is_active"] == enrollment_correct["is_active"]
    assert data["grade"] == enrollment_correct["grade"]


def test_update_student_not_found(client):
    response = client.put("/students/999", json=enrollment_correct)
    assert response.status_code == 404


def test_update_student_invalid_rate(client, enrolled):
    _contact_row, created = enrolled
    response = client.put(f"/students/{created['id']}", json={**enrollment_correct, "rate": 0})
    assert response.status_code == 422


def test_update_student_invalid_date(client, enrolled):
    _contact_row, created = enrolled
    response = client.put(f"/students/{created['id']}", json={**enrollment_correct, "start_date": "not-a-date"})
    assert response.status_code == 422


# --- DELETE ---

def test_delete_student(client):
    contact = _contact(client, first="ToDelete", last="Lastname")
    created = client.post("/students/", json={**enrollment_wrong, "contact_id": contact["id"]}).json()
    response = client.delete(f"/students/{created['id']}")
    assert response.status_code == 200  # we return deleted obj, despite it not being common practice
    assert response.json()["contact"]["first_name"] == "ToDelete"
    assert client.get(f"/students/{created['id']}").status_code == 404
    # The person survives the enrollment ending — identity and billing are separate rows.
    assert client.get(f"/contacts/{contact['id']}").status_code == 200


def test_delete_student_not_found(client):
    response = client.delete("/students/9999")
    assert response.status_code == 404


# cannot delete a student with existing lessons — historical records must be preserved
def test_delete_student_with_lessons(client, setup):
    student, _tutor, lesson = setup
    client.post("/lessons/", json=lesson)
    response = client.delete(f"/students/{student['id']}")
    assert response.status_code == 409
