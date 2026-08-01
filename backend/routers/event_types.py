from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import EventType, EventTypeAvailability
from schemas import EventTypeCreate, EventTypeUpdate, EventTypeResponse

router = APIRouter(prefix="/event_types", tags=["event_types"])


@router.get("/", response_model=list[EventTypeResponse])
def get_event_types(db: Session = Depends(get_db)):
    return db.query(EventType).all()


@router.get("/{event_type_id:int}", response_model=EventTypeResponse)
def get_event_type(event_type_id: int, db: Session = Depends(get_db)):
    db_event_type = db.query(EventType).filter(EventType.id == event_type_id).first()
    if not db_event_type:
        raise HTTPException(status_code=404, detail="Event type not found")
    return db_event_type


@router.post("/", response_model=EventTypeResponse, status_code=201)
def create_event_type(event_type_in: EventTypeCreate, db: Session = Depends(get_db)):
    # check name uniqueness
    existing = db.query(EventType).filter(EventType.name == event_type_in.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Event type with this name already exists")
    if event_type_in.min_duration_minutes is not None and event_type_in.max_duration_minutes is None:
        raise HTTPException(status_code=400, detail="max_duration_minutes is required when min_duration_minutes is set")
    if event_type_in.min_duration_minutes is not None and event_type_in.min_duration_minutes >= event_type_in.max_duration_minutes:
        raise HTTPException(status_code=400, detail="min_duration_minutes must be less than max_duration_minutes")


    db_event_type = EventType(**event_type_in.model_dump(exclude={"availability"}))
    db.add(db_event_type)
    db.flush() # to get event type id for availability entries
    for tutor_schedule in event_type_in.availability:
        event_type_availability = EventTypeAvailability(
            event_type_id=db_event_type.id,
            tutor_id=tutor_schedule.tutor_id,
            schedule_id=tutor_schedule.schedule_id,
        )
        db.add(event_type_availability)
    db.commit()
    db.refresh(db_event_type)
    return db_event_type


@router.put("/{event_type_id:int}", response_model=EventTypeResponse)
def update_event_type(event_type_id: int, event_type_in: EventTypeUpdate, db: Session = Depends(get_db)):
    db_event_type = db.query(EventType).filter(EventType.id == event_type_id).first()
    if not db_event_type:
        raise HTTPException(status_code=404, detail="Event type not found")

    # check name uniqueness for all entries besides the one we are updating
    existing = db.query(EventType).filter(EventType.name == event_type_in.name).first()
    if existing and existing.id != event_type_id:
        raise HTTPException(status_code=409, detail="Event type with this name already exists")
    if event_type_in.min_duration_minutes is not None and event_type_in.max_duration_minutes is None:
        raise HTTPException(status_code=400, detail="max_duration_minutes is required when min_duration_minutes is set")
    if event_type_in.min_duration_minutes is not None and event_type_in.min_duration_minutes >= event_type_in.max_duration_minutes:
        raise HTTPException(status_code=400, detail="min_duration_minutes must be less than max_duration_minutes")


    for field, value in event_type_in.model_dump(exclude={"availability"}).items():
        setattr(db_event_type, field, value)
    db.query(EventTypeAvailability).filter(EventTypeAvailability.event_type_id == event_type_id).delete()
    for tutor_schedule in event_type_in.availability:
        event_type_availability = EventTypeAvailability(
            event_type_id=db_event_type.id,
            tutor_id=tutor_schedule.tutor_id,
            schedule_id=tutor_schedule.schedule_id,
        )
        db.add(event_type_availability)
    db.commit()
    db.refresh(db_event_type)
    return db_event_type


@router.delete("/{event_type_id:int}", response_model=EventTypeResponse)
def delete_event_type(event_type_id: int, db: Session = Depends(get_db)):
    db_event_type = db.query(EventType).options(joinedload(EventType.availability)).filter(EventType.id == event_type_id).first()
    if not db_event_type:
        raise HTTPException(status_code=404, detail="Event type not found")
    db.delete(db_event_type)
    db.commit()
    return db_event_type