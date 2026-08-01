event_type_tutoring = {
    "name": "Tutoring Session",
    "duration_minutes": 90,
    "recurring": True,
}

event_type_custom_duration = {
    "name": "Flexible Session",
    "duration_minutes": 60,
    "min_duration_minutes": 30,
    "max_duration_minutes": 120,
    "recurring": False,
}

event_type_updated = {
    "name": "Tutoring Session Updated",
    "duration_minutes": 60,
    "recurring": True,
}

_schedule = {
    "name": "Default",
    "is_default": True,
    "timezone": "America/New_York",
    "days": [{"day_of_week": 0, "start_time": "09:00:00", "end_time": "17:00:00"}],
}


def make_availability(client):
    """Creates a tutor+schedule and returns an availability list for use in event type payloads."""
    tutor = client.post("/tutors/", json={"first_name": "T", "last_name": "T", "pay_rate": 0}).json()
    schedule = client.post("/schedules/", json={**_schedule, "tutor_id": tutor["id"]}).json()
    return [{"tutor_id": tutor["id"], "schedule_id": schedule["id"]}]


# --- CREATE ---

def test_create_event_type_required_fields(client):
    payload = {**event_type_tutoring, "availability": make_availability(client)}
    response = client.post("/event_types/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == event_type_tutoring["name"]
    assert data["duration_minutes"] == event_type_tutoring["duration_minutes"]
    assert data["recurring"] == True
    assert data["min_duration_minutes"] is None
    assert "id" in data

def test_create_event_type_custom_duration(client):
    payload = {**event_type_custom_duration, "availability": make_availability(client)}
    response = client.post("/event_types/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["min_duration_minutes"] == 30
    assert data["max_duration_minutes"] == 120

def test_create_event_type_missing_duration(client):
    # fails schema validation (no duration_minutes) — availability omitted intentionally
    response = client.post("/event_types/", json={"name": "Bad Type", "recurring": True})
    assert response.status_code == 422

def test_create_event_type_custom_duration_missing_min_max(client):
    # schema passes, router-level check fails (min without max)
    bad = {"name": "Bad Custom", "duration_minutes": 60, "min_duration_minutes": 30, "recurring": True,
           "availability": make_availability(client)}
    assert client.post("/event_types/", json=bad).status_code == 400

def test_create_event_type_duplicate_name(client):
    av = make_availability(client)
    client.post("/event_types/", json={**event_type_tutoring, "availability": av})
    assert client.post("/event_types/", json={**event_type_tutoring, "availability": av}).status_code == 409


# --- GET ---

def test_get_event_types(client):
    av = make_availability(client)
    client.post("/event_types/", json={**event_type_tutoring, "availability": av})
    client.post("/event_types/", json={**event_type_custom_duration, "availability": av})
    response = client.get("/event_types/")
    assert response.status_code == 200
    assert len(response.json()) == 2

def test_get_event_type_by_id(client):
    created = client.post("/event_types/", json={**event_type_tutoring, "availability": make_availability(client)}).json()
    response = client.get(f"/event_types/{created['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == event_type_tutoring["name"]

def test_get_event_type_not_found(client):
    assert client.get("/event_types/9999").status_code == 404


# --- UPDATE ---

def test_update_event_type(client):
    av = make_availability(client)
    created = client.post("/event_types/", json={**event_type_tutoring, "availability": av}).json()
    response = client.put(f"/event_types/{created['id']}", json={**event_type_updated, "availability": av})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == event_type_updated["name"]
    assert data["duration_minutes"] == event_type_updated["duration_minutes"]

def test_update_event_type_not_found(client):
    # body still needs to be schema-valid or FastAPI returns 422 before the router runs
    av = make_availability(client)
    assert client.put("/event_types/9999", json={**event_type_updated, "availability": av}).status_code == 404

def test_update_event_type_duplicate_name(client):
    av = make_availability(client)
    client.post("/event_types/", json={**event_type_tutoring, "availability": av})
    second = client.post("/event_types/", json={**event_type_custom_duration, "availability": av}).json()
    bad = {**event_type_updated, "name": event_type_tutoring["name"], "availability": av}
    assert client.put(f"/event_types/{second['id']}", json=bad).status_code == 409

def test_update_event_type_same_name_allowed(client):
    av = make_availability(client)
    created = client.post("/event_types/", json={**event_type_tutoring, "availability": av}).json()
    assert client.put(f"/event_types/{created['id']}", json={**event_type_tutoring, "duration_minutes": 60, "availability": av}).status_code == 200

def test_update_event_type_custom_duration_missing_min_max(client):
    av = make_availability(client)
    created = client.post("/event_types/", json={**event_type_tutoring, "availability": av}).json()
    bad = {**event_type_tutoring, "min_duration_minutes": 30, "availability": av}
    assert client.put(f"/event_types/{created['id']}", json=bad).status_code == 400


# --- DELETE ---

def test_delete_event_type(client):
    created = client.post("/event_types/", json={**event_type_tutoring, "availability": make_availability(client)}).json()
    assert client.delete(f"/event_types/{created['id']}").status_code == 200
    assert client.get(f"/event_types/{created['id']}").status_code == 404

def test_delete_event_type_not_found(client):
    assert client.delete("/event_types/9999").status_code == 404
