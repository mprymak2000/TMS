# --- CREATE ---

# hrs = 0, no fee_override, no tutor_pay_override
# positive control, only required fields
def test_create_lesson_required_fields(client, setup):
    _student, _tutor, lesson = setup
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201
    
    data = response.json()
    # passed in fields
    assert data["student_id"] == lesson["student_id"]
    assert data["tutor_id"] == lesson["tutor_id"]
    assert data["date"] == lesson["date"]
    # derived/calculated fields
    assert data["fee"] == 0
    assert data["tutor_payout"] == 0
    assert not data["is_fee_overridden"]
    assert not data["is_tutor_payout_overridden"]
    assert "id" in data

#hrs > 0, fee override, tutor pay override
# positive control, all fields
# lesson went on, fee was overriden (discount/penalty), tutor payout was overriden (bonus/penalty), and lesson was paid for
def test_create_lesson_all_fields(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "hrs": 1.5, "fee_override": 99, "tutor_pay_override": 66, "pay_status": True, "notes": "Test lesson"}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201
    
    # passed in optional fields, required tested in test_create_lesson_required_fields
    data = response.json()
    assert data["hrs"] == lesson["hrs"]
    assert data["notes"] == lesson["notes"]
    # derived/calculated fields
    assert data["fee"] == lesson["fee_override"]
    assert data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson["tutor_pay_override"]
    assert data["is_tutor_payout_overridden"]
    assert data["pay_status"]

# hrs > 0, no fee_override, no tutor_pay_override
# nomral lesson, auto calculated fee and payout based on student rate and tutor pay rate
def test_create_lesson_no_overrides(client, setup):
    student, tutor, lesson = setup
    lesson = {**lesson, "hrs": 2}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    data = response.json()
    assert data["fee"] == lesson["hrs"] * student["rate"]
    assert data["tutor_payout"] == lesson["hrs"] * tutor["pay_rate"]
    assert not data["is_fee_overridden"]
    assert not data["is_tutor_payout_overridden"]

# hrs > 0, fee_override, no tutor_pay_override
# lesson took place, but cost is adjusted (free, discount, penalty, etc)
# tutor payout is still auto calculated based on hours and tutor pay rate regardless of total fee
def test_create_lesson_fee_override_only(client, setup):
    _student, tutor, lesson = setup
    lesson = {**lesson, "hrs": 2, "fee_override": 0} # ex discount/free lesson 
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    data = response.json()
    assert data["fee"] == lesson["fee_override"]
    assert data["tutor_payout"] == lesson["hrs"] * tutor["pay_rate"]
    assert data["is_fee_overridden"]
    assert not data["is_tutor_payout_overridden"]

# hrs > 0, no fee_override, tutor_pay_override
# lesson took place, tutor payout is adjusted (bonus/penalty)
def test_create_lesson_tutor_pay_override_only(client, setup):
    student, _tutor, lesson = setup
    lesson = {**lesson, "hrs": 2, "tutor_pay_override": 100} # ex bonus for tutor
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    # tutor payout is flat and not calculated, rest is calculated normally
    data = response.json()
    assert data["fee"] == lesson["hrs"] * student["rate"]
    assert data["tutor_payout"] == lesson["tutor_pay_override"]
    assert not data["is_fee_overridden"]
    assert data["is_tutor_payout_overridden"]

# hrs = 0, fee_override, no tutor_pay_override
# lesson cancelled after cancallation deadline, full charge for standard lesson. Tutor payout is proportional to the penalty fee.
#  calculated based on the ratio of tutor pay rate to student rate (protects tutor from unfairly low payout if student rate is much higher than tutor pay rate, and protects business from overpaying tutor if student rate is much lower than tutor pay rate)
def test_create_cancelled_lesson_fee_override(client, setup):
    student, tutor, lesson = setup
    lesson = {**lesson, "hrs": 0, "fee_override": 50} # ex cancelled lesson with a penalty fee
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    data = response.json()
    assert data["fee"] == lesson["fee_override"]
    assert data["tutor_payout"] == lesson["fee_override"] * (tutor["pay_rate"] / student["rate"])
    assert data["is_fee_overridden"]
    assert not data["is_tutor_payout_overridden"]

# hrs=0, no fee_override, tutor_override — no lesson, no penalty, but tutor compensated manually
def test_create_lesson_cancelled_tutor_pay_override_only(client, setup):
    student, tutor, lesson = setup
    lesson = {**lesson, "hrs": 0, "tutor_pay_override": 30}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    data = response.json()
    assert data["fee"] == 0
    assert data["tutor_payout"] == lesson["tutor_pay_override"]
    assert not data["is_fee_overridden"]
    assert data["is_tutor_payout_overridden"]

# hrs=0, both overrides — cancelled lesson, all values explicitly overridden
def test_create_lesson_cancelled_both_overrides(client, setup):
    student, tutor, lesson = setup
    lesson = {**lesson, "hrs": 0, "fee_override": 50, "tutor_pay_override": 20}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    data = response.json()
    assert data["fee"] == lesson["fee_override"]
    assert data["tutor_payout"] == lesson["tutor_pay_override"]
    assert data["is_fee_overridden"]
    assert data["is_tutor_payout_overridden"]

def test_create_lesson_owner_as_tutor(client, setup):
    student, tutor, lesson = setup
    tutor = client.post("/tutors/", json={**tutor, "first_name": "Owner", "pay_rate": 0}).json() # make tutor the owner (no tutor payouts, all proceeds go to the house)
    lesson = {**lesson, "tutor_id": tutor["id"], "hrs": 2} 
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 201

    data = response.json()
    assert data["fee"] == lesson["hrs"] * student["rate"]
    assert data["tutor_payout"] == 0
    assert not data["is_fee_overridden"]
    assert not data["is_tutor_payout_overridden"]

# negative control, missing tutor_id  (required)
def test_create_lesson_missing_required_fields(client, setup):
    _student, _tutor, lesson = setup
    lesson = {"student_id": lesson["student_id"], "date": lesson["date"]}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 422

def test_create_lesson_invalid_date(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "date": "01-01-2025"} # incorrect format
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 422

def test_create_lesson_invalid_hrs(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "hrs": -2}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 422

def test_create_lesson_invalid_fee_override(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "fee_override": -100}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 422

def test_create_lesson_invalid_tutor_pay_override(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "tutor_pay_override": -100}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 422

def test_create_lesson_student_not_found(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "student_id" : 9999}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 404

def test_create_lesson_tutor_not_found(client, setup):
    _student, _tutor, lesson = setup
    lesson = {**lesson, "tutor_id" : 9999}
    response = client.post("/lessons/", json=lesson)
    assert response.status_code == 404

# --- GET ---

#checking and asserting required fields only
def test_get_lessons(client, setup):
    _student, _tutor, lesson1 = setup
    lesson2 ={**lesson1, "date": "2026-01-02"}
    client.post("/lessons/", json=lesson1)
    client.post("/lessons/", json=lesson2)

    response = client.get("/lessons/")
    assert response.status_code == 200
    assert len(response.json()) == 2

def test_get_lesson_by_id(client, setup):
    _student, _tutor, lesson = setup
    created = client.post("/lessons/", json=lesson).json()
    response = client.get(f"/lessons/{created['id']}")
    assert response.status_code == 200
    assert response.json()["student_id"] == lesson["student_id"]

def test_get_lesson_by_id_not_found(client):
    response = client.get("/lessons/9999")
    assert response.status_code == 404
    

# ---Update ---

def test_update_lesson(client, setup_update):
    student, tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5, "notes": "original lesson"}
    lesson_updated = {**lesson_prev, "hrs": 2, "pay_status": True, "notes": "Updated lesson"}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    # passed in check:
    assert data["hrs"] == lesson_updated["hrs"]
    assert data["pay_status"] == lesson_updated["pay_status"]
    assert data["notes"] == lesson_updated["notes"]
    # db derived/calculated fields:
    assert data["fee"] == lesson_updated["hrs"] * student["rate"]
    assert not data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["hrs"] * tutor["pay_rate"]
    assert not data["is_tutor_payout_overridden"]

# hrs > 0 (const), no fee_override -> fee override, no tutor_pay_override (const)
# update with just a fee override (apply discount, charge more)
def test_update_lesson_fee_override(client, setup_update):
    _student, tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5, "notes": "original lesson"}
    lesson_updated = {**lesson_prev, "fee_override": 200}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    assert data["fee"] == lesson_updated["fee_override"]
    assert data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["hrs"] * tutor["pay_rate"]
    assert not data["is_tutor_payout_overridden"]

# hrs > 0 (const), fee_override -> no fee override, no tutor_pay_override (const)
# update with just a fee override (apply discount, charge more)
def test_update_lesson_no_fee_override(client, setup_update):
    student, tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5, "fee_override": 200, "notes": "original lesson"}
    lesson_updated = {**lesson_prev, "fee_override": None}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    assert data["fee"] == lesson_updated["hrs"] * student["rate"]
    assert not data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["hrs"] * tutor["pay_rate"]
    assert not data["is_tutor_payout_overridden"]

# case 4: hrs > 0 (const), no tutor_pay_override -> tutor_pay_override
# tutor gets a bonus/penalty, fee calculated normally
def test_update_lesson_add_tutor_pay_override(client, setup_update):
    student, _tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5}
    lesson_updated = {**lesson_prev, "tutor_pay_override": 100}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    assert data["fee"] == lesson_updated["hrs"] * student["rate"]
    assert not data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["tutor_pay_override"]
    assert data["is_tutor_payout_overridden"]

# case 5: hrs > 0 (const), tutor_pay_override -> no tutor_pay_override
# tutor override removed, payout resets to hrs * pay_rate
def test_update_lesson_remove_tutor_pay_override(client, setup_update):
    student, tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5, "tutor_pay_override": 100}
    lesson_updated = {**lesson_prev, "tutor_pay_override": None}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    assert data["fee"] == lesson_updated["hrs"] * student["rate"]
    assert not data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["hrs"] * tutor["pay_rate"]
    assert not data["is_tutor_payout_overridden"]

# case 6: lesson occurred (hrs > 0) -> cancelled (hrs = 0) with penalty fee
# payout switches from hrs-based to ratio-based
def test_update_lesson_occurred_to_cancelled(client, setup_update):
    student, tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 2}
    lesson_updated = {**lesson_prev, "hrs": 0, "fee_override": 50}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    assert data["fee"] == lesson_updated["fee_override"]
    assert data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["fee_override"] * (tutor["pay_rate"] / student["rate"])
    assert not data["is_tutor_payout_overridden"]

# case 7: cancelled (hrs = 0, fee_override) -> lesson occurred (hrs > 0, no fee_override)
# fee and payout reset to hrs-based, both flags reset to False
def test_update_lesson_cancelled_to_occurred(client, setup_update):
    student, tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 0, "fee_override": 50}
    lesson_updated = {**lesson_prev, "hrs": 2, "fee_override": None}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200

    data = response.json()
    assert data["fee"] == lesson_updated["hrs"] * student["rate"]
    assert not data["is_fee_overridden"]
    assert data["tutor_payout"] == lesson_updated["hrs"] * tutor["pay_rate"]
    assert not data["is_tutor_payout_overridden"]

# case 8: pay_status flip — mark lesson as paid
def test_update_lesson_pay_status_flip(client, setup_update):
    _student, _tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5}
    lesson_updated = {**lesson_prev, "pay_status": True}
    created = client.post("/lessons/", json=lesson_prev).json()
    assert not created["pay_status"]
    response = client.put(f"/lessons/{created['id']}", json=lesson_updated)
    assert response.status_code == 200
    assert response.json()["pay_status"]

# case 9: lesson not found
def test_update_lesson_not_found(client, setup_update):
    _student, _tutor, lesson = setup_update
    response = client.put("/lessons/9999", json={**lesson, "hrs": 2})
    assert response.status_code == 404

# case 10: invalid date format
def test_update_lesson_invalid_date(client, setup_update):
    _student, _tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json={**lesson_prev, "date": "01-01-2025"})
    assert response.status_code == 422

# case 11: negative hrs
def test_update_lesson_invalid_hrs(client, setup_update):
    _student, _tutor, lesson = setup_update
    lesson_prev = {**lesson, "hrs": 1.5}
    created = client.post("/lessons/", json=lesson_prev).json()
    response = client.put(f"/lessons/{created['id']}", json={**lesson_prev, "hrs": -2})
    assert response.status_code == 422


# --- DELETE ---

# delete a lesson, verify it's gone
def test_delete_lesson(client, setup):
    _student, _tutor, lesson = setup
    created = client.post("/lessons/", json=lesson).json()
    response = client.delete(f"/lessons/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]

    # verify it's gone
    response = client.get(f"/lessons/{created['id']}")
    assert response.status_code == 404

# delete a lesson that doesn't exist
def test_delete_lesson_not_found(client):
    response = client.delete("/lessons/9999")
    assert response.status_code == 404


# --- BULK DELETE ---

# positive control: create multiple lessons, bulk delete, verify all gone
def test_bulk_delete_lessons(client, setup):
    _student, _tutor, lesson = setup
    id1 = client.post("/lessons/", json=lesson).json()["id"]
    id2 = client.post("/lessons/", json={**lesson, "date": "2026-02-01"}).json()["id"]
    id3 = client.post("/lessons/", json={**lesson, "date": "2026-03-01"}).json()["id"]

    response = client.request("DELETE", "/lessons/bulk_delete", json={"lesson_ids": [id1, id2, id3]})
    assert response.status_code == 200
    assert response.json()["deleted"] == 3

    # verify all gone
    assert client.get(f"/lessons/{id1}").status_code == 404
    assert client.get(f"/lessons/{id2}").status_code == 404
    assert client.get(f"/lessons/{id3}").status_code == 404

# negative control: no matching IDs
def test_bulk_delete_lessons_not_found(client):
    response = client.request("DELETE", "/lessons/bulk_delete", json={"lesson_ids": [9999, 8888]})
    assert response.status_code == 404


# --- BULK CREATE ---

# positive control: create multiple lessons, verify all returned with correct fields
def test_bulk_create_lessons(client, setup):
    student, tutor, lesson = setup
    lessons = [
        {**lesson, "date": "2026-01-01", "hrs": 1.5},
        {**lesson, "date": "2026-01-02", "hrs": 2},
        {**lesson, "date": "2026-01-03", "hrs": 0, "fee_override": 50},
    ]
    response = client.post("/lessons/bulk_create", json=lessons)
    assert response.status_code == 201

    data = response.json()
    assert len(data) == 3
    assert all("id" in l for l in data)
    assert data[0]["fee"] == lessons[0]["hrs"] * student["rate"]
    assert data[1]["fee"] == lessons[1]["hrs"] * student["rate"]
    assert data[2]["fee"] == lessons[2]["fee_override"]
    assert data[2]["tutor_payout"] == lessons[2]["fee_override"] * (tutor["pay_rate"] / student["rate"])

# atomicity: one bad student_id → nothing created
def test_bulk_create_lessons_bad_student_rolls_back(client, setup):
    _student, _tutor, lesson = setup
    lessons = [
        {**lesson, "date": "2026-01-01"},
        {**lesson, "date": "2026-01-02", "student_id": 9999},
    ]
    response = client.post("/lessons/bulk_create", json=lessons)
    assert response.status_code == 404
    assert client.get("/lessons/").json() == []

# atomicity: one bad tutor_id → nothing created
def test_bulk_create_lessons_bad_tutor_rolls_back(client, setup):
    _student, _tutor, lesson = setup
    lessons = [
        {**lesson, "date": "2026-01-01"},
        {**lesson, "date": "2026-01-02", "tutor_id": 9999},
    ]
    response = client.post("/lessons/bulk_create", json=lessons)
    assert response.status_code == 404
    assert client.get("/lessons/").json() == []