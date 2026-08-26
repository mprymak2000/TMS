import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from main import app
from database import Base, get_db
from models import Settings

SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(SQLALCHEMY_TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine)


# SQLite ships with FK enforcement off by default, unlike Postgres which always enforces it.
# The pragma is per-connection (not persisted in the DB file), so it has to be set on every
# new connection — without this, ON DELETE CASCADE/RESTRICT silently no-op in tests.
@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    # Seed the required Settings singleton. Tests use UTC so schedule/busy times align.
    # DST-specific tests call _set_business_tz(db, "America/New_York") to override.
    with TestingSessionLocal() as s:
        if not s.query(Settings).filter(Settings.id == 1).first():
            s.add(Settings(id=1, business_timezone="UTC"))
            s.commit()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def setup(client):
    student_test = {
        "first_name": "Student",
        "last_name": "Test",
        "rate": 50,
        "start_date": "2026-01-01",
        "is_active": True
    }
    tutor_test = {
        "first_name": "Tutor",
        "last_name": "Test",
        "pay_rate": 30
        }
    student = client.post("/students/", json=student_test).json()
    tutor = client.post("/tutors/", json=tutor_test).json()
    lesson = {
        "student_id": student["id"],
        "tutor_id": tutor["id"],
        "date": "2024-01-01"
    }
    return student, tutor, lesson

@pytest.fixture
def setup_update(setup):
    student, tutor, lesson = setup
    return student, tutor, {**lesson, "pay_status": False}