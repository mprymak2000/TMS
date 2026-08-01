from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database import get_db
from models import EventTypeAvailability, EventType, Tutor, Schedule
from schemas import EventTypeAvailabilityCreate, EventTypeAvailabilityResponse

router = APIRouter(prefix="/event_type_availability", tags=["event_type_availability"])


@router.get("/", response_model=list[EventTypeAvailabilityResponse])
def get_event_type_availabilities(db: Session = Depends(get_db)):
    return db.query(EventTypeAvailability).all()


@router.get("/{availability_id}", response_model=EventTypeAvailabilityResponse)
def get_event_type_availability(availability_id: int, db: Session = Depends(get_db)):
    db_availability = db.query(EventTypeAvailability).filter(EventTypeAvailability.id == availability_id).first()
    if not db_availability:
        raise HTTPException(status_code=404, detail="Event type availability not found.")
    return db_availability


@router.post("/", response_model=EventTypeAvailabilityResponse, status_code=201)
def create_event_type_availability(availability_in: EventTypeAvailabilityCreate, db: Session = Depends(get_db)):
    if not db.query(EventType).filter(EventType.id == availability_in.event_type_id).first():
        raise HTTPException(status_code=404, detail="Event type not found.")
    if not db.query(Tutor).filter(Tutor.id == availability_in.tutor_id).first():
        raise HTTPException(status_code=404, detail="Tutor not found.")
    db_schedule = db.query(Schedule).filter(Schedule.id == availability_in.schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    if db_schedule.tutor_id != availability_in.tutor_id:
        raise HTTPException(status_code=400, detail="Schedule does not belong to the specified tutor.")

    new_availability = EventTypeAvailability(**availability_in.model_dump())
    db.add(new_availability)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This tutor is already linked to this event type.")
    db.refresh(new_availability)
    return new_availability


@router.put("/{availability_id}", response_model=EventTypeAvailabilityResponse)
def update_event_type_availability(availability_id: int, availability_in: EventTypeAvailabilityCreate, db: Session = Depends(get_db)):
    db_availability = db.query(EventTypeAvailability).filter(EventTypeAvailability.id == availability_id).first()
    if not db_availability:
        raise HTTPException(status_code=404, detail="Event type availability not found.")
    db_schedule = db.query(Schedule).filter(Schedule.id == availability_in.schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    if db_schedule.tutor_id != db_availability.tutor_id:
        raise HTTPException(status_code=400, detail="Schedule does not belong to this tutor.")

    db_availability.schedule_id = availability_in.schedule_id
    db.commit()
    db.refresh(db_availability)
    return db_availability
