from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database import get_db
from models import BookingLinkAvailability, BookingLink, Tutor, Schedule
from schemas import BookingLinkAvailabilityCreate, BookingLinkAvailabilityResponse

router = APIRouter(prefix="/booking_link_availability", tags=["booking_link_availability"])


@router.get("/", response_model=list[BookingLinkAvailabilityResponse])
def get_booking_link_availabilities(db: Session = Depends(get_db)):
    return db.query(BookingLinkAvailability).all()


@router.get("/{availability_id}", response_model=BookingLinkAvailabilityResponse)
def get_booking_link_availability(availability_id: int, db: Session = Depends(get_db)):
    db_availability = db.query(BookingLinkAvailability).filter(BookingLinkAvailability.id == availability_id).first()
    if not db_availability:
        raise HTTPException(status_code=404, detail="Booking link availability not found.")
    return db_availability


@router.post("/", response_model=BookingLinkAvailabilityResponse, status_code=201)
def create_booking_link_availability(availability_in: BookingLinkAvailabilityCreate, db: Session = Depends(get_db)):
    if not db.query(BookingLink).filter(BookingLink.id == availability_in.booking_link_id).first():
        raise HTTPException(status_code=404, detail="Booking link not found.")
    if not db.query(Tutor).filter(Tutor.id == availability_in.tutor_id).first():
        raise HTTPException(status_code=404, detail="Tutor not found.")
    db_schedule = db.query(Schedule).filter(Schedule.id == availability_in.schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    if db_schedule.tutor_id != availability_in.tutor_id:
        raise HTTPException(status_code=400, detail="Schedule does not belong to the specified tutor.")

    new_availability = BookingLinkAvailability(**availability_in.model_dump())
    db.add(new_availability)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This tutor is already linked to this booking link.")
    db.refresh(new_availability)
    return new_availability


@router.put("/{availability_id}", response_model=BookingLinkAvailabilityResponse)
def update_booking_link_availability(availability_id: int, availability_in: BookingLinkAvailabilityCreate, db: Session = Depends(get_db)):
    db_availability = db.query(BookingLinkAvailability).filter(BookingLinkAvailability.id == availability_id).first()
    if not db_availability:
        raise HTTPException(status_code=404, detail="Booking link availability not found.")
    db_schedule = db.query(Schedule).filter(Schedule.id == availability_in.schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    if db_schedule.tutor_id != db_availability.tutor_id:
        raise HTTPException(status_code=400, detail="Schedule does not belong to this tutor.")

    db_availability.schedule_id = availability_in.schedule_id
    db.commit()
    db.refresh(db_availability)
    return db_availability
