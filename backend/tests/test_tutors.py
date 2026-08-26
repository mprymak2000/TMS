test_tutor = {
    "first_name": "Test",
    "last_name": "Tutor",
    "pay_rate": 40,
}


# --- CREATE ---

def test_create_tutor(client):
    response = client.post("/tutors/", json=test_tutor)
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == test_tutor["first_name"]
    assert data["last_name"] == test_tutor["last_name"]
    assert data["pay_rate"] == test_tutor["pay_rate"]
    assert data["is_active"]  # default True
    assert "id" in data

# all fields including is_active=False (inactive/retired tutor) and calendar_id none
# auto defaults to false / none
def test_create_tutor_all_fields(client):
    response = client.post("/tutors/", json={**test_tutor, "is_active": False})
    assert response.status_code == 201
    assert not response.json()["is_active"]

# pay_rate=0 is valid (owner/operator tutor)
def test_create_tutor_zero_pay_rate(client):
    response = client.post("/tutors/", json={**test_tutor, "pay_rate": 0})
    assert response.status_code == 201
    assert response.json()["pay_rate"] == 0

# missing rate
def test_create_tutor_missing_required_fields(client):
    response = client.post("/tutors/", json={"first_name": "Test", "last_name": "rate"})
    assert response.status_code == 422

# negative pay rate
def test_create_tutor_invalid_pay_rate(client):
    response = client.post("/tutors/", json={**test_tutor, "pay_rate": -10})
    assert response.status_code == 422


# --- GET ---
def test_get_tutors(client):
    client.post("/tutors/", json=test_tutor)
    client.post("/tutors/", json={**test_tutor, "first_name": "Another"})
    response = client.get("/tutors/")
    assert response.status_code == 200
    assert len(response.json()) == 2
    # above we dont check fields bc it was already done, we just checking that everyone was added


def test_get_tutor_by_id(client):
    created = client.post("/tutors/", json=test_tutor).json()
    response = client.get(f"/tutors/{created['id']}")
    assert response.status_code == 200
    assert response.json()["first_name"] == test_tutor["first_name"]
    #above we just check that the right person was returned, not all fields

def test_get_tutor_by_id_not_found(client):
    response = client.get("/tutors/9999")
    assert response.status_code == 404


# --- UPDATE ---

def test_update_tutor(client):
    created = client.post("/tutors/", json=test_tutor).json()
    tutor_updated = {**test_tutor, "pay_rate": 35, "is_active": True} # ex tutor got raise
    response = client.put(f"/tutors/{created['id']}", json=tutor_updated)
    assert response.status_code == 200
    assert response.json()["pay_rate"] == tutor_updated["pay_rate"]
    assert response.json()["is_active"] == tutor_updated["is_active"]

# deactivate a tutor (ex: tutor left)
def test_update_tutor_deactivate(client):
    created = client.post("/tutors/", json=test_tutor).json()
    response = client.put(f"/tutors/{created['id']}", json={**test_tutor, "is_active": False})
    assert response.status_code == 200
    assert not response.json()["is_active"]

def test_update_tutor_not_found(client):
    response = client.put("/tutors/9999", json={**test_tutor, "pay_rate": 35, "is_active": True})
    assert response.status_code == 404

def test_update_tutor_invalid_pay_rate(client):
    created = client.post("/tutors/", json=test_tutor).json()
    bad = {**test_tutor, "pay_rate": -10, "is_active": True}
    response = client.put(f"/tutors/{created['id']}", json=bad)
    assert response.status_code == 422


# --- DELETE ---

def test_delete_tutor(client):
    created = client.post("/tutors/", json=test_tutor).json()
    response = client.delete(f"/tutors/{created['id']}")
    assert response.status_code == 200 #we return deleted obj, despite it not being common practice
    assert response.json()["first_name"] == test_tutor["first_name"]
    #confirm it's actually deleted
    response = client.get(f"/tutors/{created['id']}")
    assert response.status_code == 404

def test_delete_tutor_not_found(client):
    response = client.delete("/tutors/9999")
    assert response.status_code == 404

# cannot delete a tutor with existing lessons — historical records must be preserved
def test_delete_tutor_with_lessons(client, setup):
    _student, tutor, lesson = setup
    client.post("/lessons/", json=lesson)
    response = client.delete(f"/tutors/{tutor['id']}")
    assert response.status_code == 409

# a tutor with schedules but zero bookings is a normal, freely-deletable state — the schedules
# cascade away with it (nothing worth preserving once the tutor itself is gone)
def test_delete_tutor_cascades_schedules(client):
    tutor = client.post("/tutors/", json=test_tutor).json()
    schedule = client.post("/schedules/", json={
        "tutor_id": tutor["id"],
        "name": "Regular Hours",
        "is_default": True,
        "timezone": "America/New_York",
        "days": [{"day_of_week": 0, "start_time": "16:00:00", "end_time": "18:00:00"}],
    }).json()
    response = client.delete(f"/tutors/{tutor['id']}")
    assert response.status_code == 200
    assert client.get(f"/schedules/{schedule['id']}").status_code == 404