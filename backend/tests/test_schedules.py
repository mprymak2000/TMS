tutor_test = {"first_name": "Tutor", "last_name": "Test", "pay_rate": 0}

schedule_regular = {
    "name": "Regular Hours",
    "is_default": True,
    "timezone": "America/New_York",
    "days": [
        {"day_of_week": 0, "start_time": "16:00:00", "end_time": "18:00:00"},
        {"day_of_week": 2, "start_time": "16:00:00", "end_time": "18:00:00"},
    ]
}

schedule_summer = {
    "name": "Summer Hours",
    "is_default": False,
    "timezone": "America/New_York",
    "days": [
        {"day_of_week": 1, "start_time": "10:00:00", "end_time": "12:00:00"},
    ]
}


# --- HELPERS ---

def create_tutor(client):
    return client.post("/tutors/", json=tutor_test).json()


# --- CREATE ---

def test_create_schedule(client):
    tutor = create_tutor(client)
    payload = {**schedule_regular, "tutor_id": tutor["id"]}
    response = client.post("/schedules/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == schedule_regular["name"]
    assert data["is_default"] == True
    assert data["tutor_id"] == tutor["id"]
    assert len(data["days"]) == 2
    assert "id" in data

def test_create_schedule_tutor_not_found(client):
    payload = {**schedule_regular, "tutor_id": 9999}
    response = client.post("/schedules/", json=payload)
    assert response.status_code == 404

def test_create_schedule_no_days(client):
    tutor = create_tutor(client)
    payload = {"tutor_id": tutor["id"], "name": "Empty", "is_default": False, "days": []}
    response = client.post("/schedules/", json=payload)
    assert response.status_code == 422

def test_create_schedule_invalid_day_of_week(client):
    tutor = create_tutor(client)
    payload = {
        "tutor_id": tutor["id"], "name": "Bad", "is_default": False,
        "days": [{"day_of_week": 7, "start_time": "09:00:00", "end_time": "10:00:00"}]
    }
    response = client.post("/schedules/", json=payload)
    assert response.status_code == 422

def test_create_schedule_end_before_start(client):
    tutor = create_tutor(client)
    payload = {
        "tutor_id": tutor["id"], "name": "Bad", "is_default": False,
        "days": [{"day_of_week": 0, "start_time": "18:00:00", "end_time": "16:00:00"}]
    }
    response = client.post("/schedules/", json=payload)
    assert response.status_code == 422

def test_create_schedule_flips_existing_default(client):
    tutor = create_tutor(client)
    first = client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]}).json()
    assert first["is_default"] == True

    second = client.post("/schedules/", json={**schedule_summer, "is_default": True, "tutor_id": tutor["id"]}).json()
    assert second["is_default"] == True

    updated_first = client.get(f"/schedules/{first['id']}").json()
    assert updated_first["is_default"] == False

def test_create_schedule_multiple_days_same_weekday(client):
    tutor = create_tutor(client)
    payload = {
        "tutor_id": tutor["id"], "name": "Split Monday", "is_default": True,
        "timezone": "America/New_York",
        "days": [
            {"day_of_week": 0, "start_time": "09:00:00", "end_time": "11:00:00"},
            {"day_of_week": 0, "start_time": "14:00:00", "end_time": "16:00:00"},
        ]
    }
    response = client.post("/schedules/", json=payload)
    assert response.status_code == 201
    assert len(response.json()["days"]) == 2


# --- GET ---

def test_get_schedules(client):
    tutor = create_tutor(client)
    client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]})
    client.post("/schedules/", json={**schedule_summer, "tutor_id": tutor["id"]})
    response = client.get("/schedules/")
    assert response.status_code == 200
    assert len(response.json()) == 2

def test_get_schedule_by_id(client):
    tutor = create_tutor(client)
    created = client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]}).json()
    response = client.get(f"/schedules/{created['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == schedule_regular["name"]

def test_get_schedule_not_found(client):
    response = client.get("/schedules/9999")
    assert response.status_code == 404


# --- UPDATE ---

def test_update_schedule(client):
    tutor = create_tutor(client)
    created = client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]}).json()
    updated_payload = {
        "name": "Updated Hours",
        "is_default": True,
        "timezone": "America/New_York",
        "days": [{"day_of_week": 4, "start_time": "15:00:00", "end_time": "17:00:00"}]
    }
    response = client.put(f"/schedules/{created['id']}", json=updated_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Hours"
    assert len(data["days"]) == 1
    assert data["days"][0]["day_of_week"] == 4

def test_update_schedule_not_found(client):
    payload = {**schedule_regular, "days": [{"day_of_week": 0, "start_time": "09:00:00", "end_time": "10:00:00"}]}
    response = client.put("/schedules/9999", json=payload)
    assert response.status_code == 404

def test_update_schedule_block_unset_default(client):
    tutor = create_tutor(client)
    created = client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]}).json()
    payload = {**schedule_regular, "is_default": False, "days": schedule_regular["days"]}
    response = client.put(f"/schedules/{created['id']}", json=payload)
    assert response.status_code == 400

def test_update_schedule_false_to_true_flips_old_default(client):
    tutor = create_tutor(client)
    first = client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]}).json()
    second = client.post("/schedules/", json={**schedule_summer, "tutor_id": tutor["id"]}).json()

    payload = {**schedule_summer, "is_default": True, "days": schedule_summer["days"]}
    client.put(f"/schedules/{second['id']}", json=payload)

    assert client.get(f"/schedules/{first['id']}").json()["is_default"] == False
    assert client.get(f"/schedules/{second['id']}").json()["is_default"] == True


# --- DELETE ---

def test_delete_schedule(client):
    tutor = create_tutor(client)
    client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]})
    non_default = client.post("/schedules/", json={**schedule_summer, "tutor_id": tutor["id"]}).json()
    response = client.delete(f"/schedules/{non_default['id']}")
    assert response.status_code == 200
    assert client.get(f"/schedules/{non_default['id']}").status_code == 404

def test_delete_default_schedule_blocked(client):
    tutor = create_tutor(client)
    created = client.post("/schedules/", json={**schedule_regular, "tutor_id": tutor["id"]}).json()
    response = client.delete(f"/schedules/{created['id']}")
    assert response.status_code == 400

def test_delete_schedule_not_found(client):
    response = client.delete("/schedules/9999")
    assert response.status_code == 404
