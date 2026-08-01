student_a = {
    "first_name": "Student",
    "last_name": "A",
    "rate": 55,
    "start_date": "2021-04-01",
}

student_b = {
    "first_name": "Student",
    "last_name": "B",
    "rate": 45,
    "start_date": "2021-04-01"
}

#will be updated in every mutable field
student_c_wrong = {
    "first_name": "Student",
    "last_name": "C",
    "rate": 1,
    "start_date": "1999-01-01",
    "is_active": False,
    "grade": 150,
    "birthday": "2009-01-01",
    "email": "email@gmail.com"
}

student_c_correct = {
    "first_name": "Student",
    "last_name": "C",
    "rate": 65,
    "start_date": "2022-10-01",
    "is_active": True,
    "grade": 11,
    "birthday": "2009-01-01",
    "email": "studentc@example.com"
}


#tests all fields, to be deleted
student_to_delete = {
    "first_name": "ToDelete",
    "last_name": "lastname",
    "rate": 1,
    "start_date": "1999-01-01",
    "is_active": False,
    "grade": 150,
    "birthday": "1999-01-01",
    "email": "email@gmail.com"
}

# --- CREATE ---

# positive control, only required fields
def test_create_student_required_fields(client):
    response = client.post("/students/", json=student_a)
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == student_a["first_name"]
    assert data["last_name"] == student_a["last_name"]
    assert data["rate"] == student_a["rate"]
    assert data["start_date"] == student_a["start_date"]
    assert data["is_active"] #true is default value
    assert data["grade"] is None
    assert data["birthday"] is None
    assert data["email"] is None
    assert "id" in data

# positive control, all fields
def test_create_student_all_fields(client):
    response = client.post("/students/", json=student_c_wrong)
    assert response.status_code == 201
    data = response.json()
    assert not data["is_active"]
    assert data["birthday"] == student_c_wrong["birthday"]
    assert data["email"] == student_c_wrong["email"]
    assert "id" in data

# missing rate and start date 
def test_create_student_missing_required_fields(client):
    response = client.post("/students/", json = {"first_name": "Student", "last_name": "rate_will_be_missing"})
    assert response.status_code == 422

# negative hourly rate per hr of teaching 
def test_create_student_invalid_rate(client):
    bad = {**student_a, "rate": -10}
    response = client.post("/students/", json=bad)
    assert response.status_code == 422

# invalid start date format
def test_create_student_invalid_date(client):
    bad = {**student_a, "start_date": "not-a-date"}
    response = client.post("/students/", json=bad)
    assert response.status_code == 422


# --- GET ---

def test_get_students(client):
    client.post("/students/", json=student_a)
    client.post("/students/", json=student_b)
    response = client.get("/students/")
    assert response.status_code == 200
    assert len(response.json()) == 2
    # above we dont check fields bc it was already done, we just checking that everyone was added

def test_get_student_by_id(client):
    created_student = client.post("/students/", json=student_a).json()
    response = client.get(f"/students/{created_student['id']}")
    assert response.status_code == 200
    assert response.json()["first_name"] == student_a["first_name"]
    #above we just check that the right person was returned, not all fields

def test_get_student_by_id_not_found(client):
    response = client.get("/students/9999")
    assert response.status_code == 404


# --- UPDATE ---

def test_update_student(client):
    created = client.post("/students/", json=student_c_wrong).json()
    response = client.put(f"/students/{created['id']}", json=student_c_correct)
    assert response.status_code == 200
    data = response.json()
    #only asserting fields that were updated (all mutable fields)
    assert data["rate"] == student_c_correct["rate"]
    assert data["start_date"] == student_c_correct["start_date"]
    assert data["is_active"] == student_c_correct["is_active"]
    assert data["grade"] == student_c_correct["grade"]
    assert data["email"] == student_c_correct["email"]

def test_update_student_not_found(client):
    response = client.put("/students/999", json=student_c_correct)
    assert response.status_code == 404

def test_update_student_invalid_rate(client):
    created = client.post("/students/", json=student_c_wrong).json()
    bad = {**student_c_correct, "rate": 0}
    response = client.put(f"/students/{created['id']}", json=bad)
    assert response.status_code == 422

def test_update_student_invalid_date(client):
    created = client.post("/students/", json=student_c_wrong).json()
    bad = {**student_c_wrong, "start_date": "not-a-date"}
    response = client.put(f"/students/{created['id']}", json=bad)
    assert response.status_code == 422


# --- DELETE ---

def test_delete_student(client):
    created = client.post("/students/", json = student_to_delete).json()
    response = client.delete(f"/students/{created['id']}")
    assert response.status_code == 200 # we return deleted obj, despite it not being common practice
    assert response.json()["first_name"] == student_to_delete["first_name"]
    #confirm it's actually deleted
    response = client.get(f"/students/{created['id']}")
    assert response.status_code == 404

def test_delete_student_not_found(client):
    response = client.delete("/students/9999")
    assert response.status_code == 404

# cannot delete a student with existing lessons — historical records must be preserved
def test_delete_student_with_lessons(client, setup):
    student, _tutor, lesson = setup
    client.post("/lessons/", json=lesson)
    response = client.delete(f"/students/{student['id']}")
    assert response.status_code == 409

