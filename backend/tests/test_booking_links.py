booking_link_tutoring = {
    "slug": "tutoring-session",
    "duration_minutes": 90,
    "recurring": True,
}

booking_link_custom_duration = {
    "slug": "flexible-session",
    "duration_minutes": 60,
    "min_duration_minutes": 30,
    "max_duration_minutes": 120,
    "recurring": False,
}

booking_link_updated = {
    "slug": "tutoring-session-updated",
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

def test_create_booking_link_required_fields(client):
    payload = {**booking_link_tutoring, "availability": make_availability(client)}
    response = client.post("/booking_links/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["slug"] == booking_link_tutoring["slug"]
    assert data["duration_minutes"] == booking_link_tutoring["duration_minutes"]
    assert data["recurring"] == True
    assert data["min_duration_minutes"] is None
    assert "id" in data

def test_create_booking_link_custom_duration(client):
    payload = {**booking_link_custom_duration, "availability": make_availability(client)}
    response = client.post("/booking_links/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["min_duration_minutes"] == 30
    assert data["max_duration_minutes"] == 120

def test_create_booking_link_missing_duration(client):
    # fails schema validation (no duration_minutes) — availability omitted intentionally
    response = client.post("/booking_links/", json={"slug": "bad-type", "recurring": True})
    assert response.status_code == 422

def test_create_booking_link_custom_duration_missing_min_max(client):
    # schema passes, router-level check fails (min without max)
    bad = {"slug": "bad-custom", "duration_minutes": 60, "min_duration_minutes": 30, "recurring": True,
           "availability": make_availability(client)}
    assert client.post("/booking_links/", json=bad).status_code == 400

def test_create_booking_link_duplicate_slug(client):
    av = make_availability(client)
    client.post("/booking_links/", json={**booking_link_tutoring, "availability": av})
    assert client.post("/booking_links/", json={**booking_link_tutoring, "availability": av}).status_code == 409


# --- GET ---

def test_get_booking_links(client):
    av = make_availability(client)
    client.post("/booking_links/", json={**booking_link_tutoring, "availability": av})
    client.post("/booking_links/", json={**booking_link_custom_duration, "availability": av})
    response = client.get("/booking_links/")
    assert response.status_code == 200
    assert len(response.json()) == 2

def test_get_booking_link_by_id(client):
    created = client.post("/booking_links/", json={**booking_link_tutoring, "availability": make_availability(client)}).json()
    response = client.get(f"/booking_links/{created['id']}")
    assert response.status_code == 200
    assert response.json()["slug"] == booking_link_tutoring["slug"]

def test_get_booking_link_not_found(client):
    assert client.get("/booking_links/9999").status_code == 404


# --- UPDATE ---

def test_update_booking_link(client):
    av = make_availability(client)
    created = client.post("/booking_links/", json={**booking_link_tutoring, "availability": av}).json()
    response = client.put(f"/booking_links/{created['id']}", json={**booking_link_updated, "availability": av})
    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == booking_link_updated["slug"]
    assert data["duration_minutes"] == booking_link_updated["duration_minutes"]

def test_update_booking_link_not_found(client):
    # body still needs to be schema-valid or FastAPI returns 422 before the router runs
    av = make_availability(client)
    assert client.put("/booking_links/9999", json={**booking_link_updated, "availability": av}).status_code == 404

def test_update_booking_link_duplicate_slug(client):
    av = make_availability(client)
    client.post("/booking_links/", json={**booking_link_tutoring, "availability": av})
    second = client.post("/booking_links/", json={**booking_link_custom_duration, "availability": av}).json()
    bad = {**booking_link_updated, "slug": booking_link_tutoring["slug"], "availability": av}
    assert client.put(f"/booking_links/{second['id']}", json=bad).status_code == 409

def test_update_booking_link_same_slug_allowed(client):
    av = make_availability(client)
    created = client.post("/booking_links/", json={**booking_link_tutoring, "availability": av}).json()
    assert client.put(f"/booking_links/{created['id']}", json={**booking_link_tutoring, "duration_minutes": 60, "availability": av}).status_code == 200

def test_update_booking_link_custom_duration_missing_min_max(client):
    av = make_availability(client)
    created = client.post("/booking_links/", json={**booking_link_tutoring, "availability": av}).json()
    bad = {**booking_link_tutoring, "min_duration_minutes": 30, "availability": av}
    assert client.put(f"/booking_links/{created['id']}", json=bad).status_code == 400


# --- DELETE ---

def test_delete_booking_link(client):
    created = client.post("/booking_links/", json={**booking_link_tutoring, "availability": make_availability(client)}).json()
    assert client.delete(f"/booking_links/{created['id']}").status_code == 200
    assert client.get(f"/booking_links/{created['id']}").status_code == 404

def test_delete_booking_link_not_found(client):
    assert client.delete("/booking_links/9999").status_code == 404
