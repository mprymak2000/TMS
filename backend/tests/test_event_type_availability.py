tutor_a = {"first_name": "Tutor", "last_name": "A", "pay_rate": 0}
tutor_b = {"first_name": "Tutor", "last_name": "B", "pay_rate": 25}

event_type_tutoring = {
    "name": "Tutoring Session",
    "duration_minutes": 90,
    "recurring": True,
}

schedule_payload = {
    "name": "Regular Hours",
    "is_default": True,
    "timezone": "America/New_York",
    "days": [
        {"day_of_week": 0, "start_time": "16:00:00", "end_time": "18:00:00"},
    ]
}


# --- HELPERS ---

def create_tutor(client, data):
    return client.post("/tutors/", json=data).json()

def create_event_type(client):
    # uses a separate dummy tutor+schedule so tests can freely add their own tutors
    dummy = client.post("/tutors/", json={"first_name": "Dummy", "last_name": "Tutor", "pay_rate": 0}).json()
    dummy_sched = create_schedule(client, dummy["id"])
    return client.post("/event_types/", json={
        **event_type_tutoring,
        "availability": [{"tutor_id": dummy["id"], "schedule_id": dummy_sched["id"]}],
    }).json()

def create_schedule(client, tutor_id):
    return client.post("/schedules/", json={**schedule_payload, "tutor_id": tutor_id}).json()


# --- CREATE ---

def test_create_event_type_availability(client):
    tutor = create_tutor(client, tutor_a)
    event_type = create_event_type(client)
    schedule = create_schedule(client, tutor["id"])

    response = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"],
        "tutor_id": tutor["id"],
        "schedule_id": schedule["id"],
    })
    assert response.status_code == 201
    data = response.json()
    assert data["event_type_id"] == event_type["id"]
    assert data["tutor_id"] == tutor["id"]
    assert data["schedule_id"] == schedule["id"]
    assert "id" in data

def test_create_availability_event_type_not_found(client):
    tutor = create_tutor(client, tutor_a)
    schedule = create_schedule(client, tutor["id"])
    response = client.post("/event_type_availability/", json={
        "event_type_id": 9999,
        "tutor_id": tutor["id"],
        "schedule_id": schedule["id"],
    })
    assert response.status_code == 404

def test_create_availability_tutor_not_found(client):
    event_type = create_event_type(client)
    tutor = create_tutor(client, tutor_a)
    schedule = create_schedule(client, tutor["id"])
    response = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"],
        "tutor_id": 9999,
        "schedule_id": schedule["id"],
    })
    assert response.status_code == 404

def test_create_availability_schedule_not_found(client):
    tutor = create_tutor(client, tutor_a)
    event_type = create_event_type(client)
    response = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"],
        "tutor_id": tutor["id"],
        "schedule_id": 9999,
    })
    assert response.status_code == 404

def test_create_availability_schedule_wrong_tutor(client):
    tutor1 = create_tutor(client, tutor_a)
    tutor2 = create_tutor(client, tutor_b)
    event_type = create_event_type(client)
    schedule = create_schedule(client, tutor1["id"])
    response = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"],
        "tutor_id": tutor2["id"],
        "schedule_id": schedule["id"],
    })
    assert response.status_code == 400

def test_create_availability_duplicate_tutor_event_type(client):
    tutor = create_tutor(client, tutor_a)
    event_type = create_event_type(client)
    schedule = create_schedule(client, tutor["id"])
    payload = {"event_type_id": event_type["id"], "tutor_id": tutor["id"], "schedule_id": schedule["id"]}
    client.post("/event_type_availability/", json=payload)
    response = client.post("/event_type_availability/", json=payload)
    assert response.status_code == 409


# --- GET ---

def test_get_event_type_availabilities(client):
    tutor1 = create_tutor(client, tutor_a)
    tutor2 = create_tutor(client, tutor_b)
    event_type = create_event_type(client)
    schedule1 = create_schedule(client, tutor1["id"])
    schedule2 = create_schedule(client, tutor2["id"])
    client.post("/event_type_availability/", json={"event_type_id": event_type["id"], "tutor_id": tutor1["id"], "schedule_id": schedule1["id"]})
    client.post("/event_type_availability/", json={"event_type_id": event_type["id"], "tutor_id": tutor2["id"], "schedule_id": schedule2["id"]})
    response = client.get("/event_type_availability/")
    assert response.status_code == 200
    assert len(response.json()) == 3

def test_get_event_type_availability_by_id(client):
    tutor = create_tutor(client, tutor_a)
    event_type = create_event_type(client)
    schedule = create_schedule(client, tutor["id"])
    created = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"], "tutor_id": tutor["id"], "schedule_id": schedule["id"]
    }).json()
    response = client.get(f"/event_type_availability/{created['id']}")
    assert response.status_code == 200

def test_get_event_type_availability_not_found(client):
    response = client.get("/event_type_availability/9999")
    assert response.status_code == 404


# --- UPDATE ---

def test_update_event_type_availability(client):
    tutor = create_tutor(client, tutor_a)
    event_type = create_event_type(client)
    schedule1 = create_schedule(client, tutor["id"])
    schedule2 = client.post("/schedules/", json={
        "tutor_id": tutor["id"], "name": "Summer Hours", "is_default": False,
        "timezone": "America/New_York",
        "days": [{"day_of_week": 1, "start_time": "10:00:00", "end_time": "12:00:00"}]
    }).json()
    created = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"], "tutor_id": tutor["id"], "schedule_id": schedule1["id"]
    }).json()

    response = client.put(f"/event_type_availability/{created['id']}", json={
        "event_type_id": event_type["id"], "tutor_id": tutor["id"], "schedule_id": schedule2["id"]
    })
    assert response.status_code == 200
    assert response.json()["schedule_id"] == schedule2["id"]

def test_update_availability_wrong_tutor_schedule(client):
    tutor1 = create_tutor(client, tutor_a)
    tutor2 = create_tutor(client, tutor_b)
    event_type = create_event_type(client)
    schedule1 = create_schedule(client, tutor1["id"])
    schedule2 = create_schedule(client, tutor2["id"])
    created = client.post("/event_type_availability/", json={
        "event_type_id": event_type["id"], "tutor_id": tutor1["id"], "schedule_id": schedule1["id"]
    }).json()
    response = client.put(f"/event_type_availability/{created['id']}", json={
        "event_type_id": event_type["id"], "tutor_id": tutor1["id"], "schedule_id": schedule2["id"]
    })
    assert response.status_code == 400

def test_update_availability_not_found(client):
    tutor = create_tutor(client, tutor_a)
    schedule = create_schedule(client, tutor["id"])
    event_type = create_event_type(client)
    response = client.put("/event_type_availability/9999", json={
        "event_type_id": event_type["id"], "tutor_id": tutor["id"], "schedule_id": schedule["id"]
    })
    assert response.status_code == 404
