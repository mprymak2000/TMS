from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from database import get_db
from models import BookingType, BookingLink, Booking, BookingSeries
from schemas import BookingTypeCreate, BookingTypeUpdate, BookingTypeResponse, BookingTypeUsage

router = APIRouter(prefix="/booking_types", tags=["booking_types"])


def _label_taken(db: Session, label: str, ignore_type_id: int | None = None) -> bool:
    """Local helper for the create/update label check — nothing else calls it.

    Compared case-insensitively so "Consultation" and "consultation" can't both exist; the point of a
    type is that one kind is one row. `ignore_type_id` is only passed by update, so a type keeping its
    own label doesn't collide with itself.
    """
    q = db.query(BookingType).filter(func.lower(BookingType.label) == label.lower())
    if ignore_type_id is not None:
        q = q.filter(BookingType.id != ignore_type_id)
    return db.query(q.exists()).scalar()


@router.get("/", response_model=list[BookingTypeResponse])
def get_booking_types(db: Session = Depends(get_db)):
    return db.query(BookingType).order_by(func.lower(BookingType.label)).all()


@router.post("/", response_model=BookingTypeResponse, status_code=201)
def create_booking_type(type_in: BookingTypeCreate, db: Session = Depends(get_db)):
    if _label_taken(db, type_in.label):
        raise HTTPException(status_code=409, detail=f"A type named '{type_in.label}' already exists")

    db_type = BookingType(**type_in.model_dump())
    db.add(db_type)
    db.commit()
    db.refresh(db_type)
    return db_type


@router.put("/{booking_type_id}", response_model=BookingTypeResponse)
def update_booking_type(booking_type_id: int, type_in: BookingTypeUpdate, db: Session = Depends(get_db)):
    """A rename is a correction, not a fork: everything pointing here relabels, past bookings included.
    That propagation is the whole reason a type is a row and not a string copied onto each booking."""
    db_type = db.query(BookingType).filter(BookingType.id == booking_type_id).first()
    if not db_type:
        raise HTTPException(status_code=404, detail="Booking type not found")
    if _label_taken(db, type_in.label, ignore_type_id=booking_type_id):
        raise HTTPException(status_code=409, detail=f"A type named '{type_in.label}' already exists")

    for field, value in type_in.model_dump().items():
        setattr(db_type, field, value)
    db.commit()
    db.refresh(db_type)
    return db_type


@router.get("/{booking_type_id}/usage", response_model=BookingTypeUsage)
def get_booking_type_usage(booking_type_id: int, db: Session = Depends(get_db)):
    """What a delete would cost, fetched when the user clicks delete rather than on every picker open."""
    if not db.query(db.query(BookingType).filter(BookingType.id == booking_type_id).exists()).scalar():
        raise HTTPException(status_code=404, detail="Booking type not found")

    counts = {
        name: db.query(func.count(model.id)).filter(model.booking_type_id == booking_type_id).scalar()
        for name, model in (("links", BookingLink), ("bookings", Booking), ("series", BookingSeries))
    }
    return BookingTypeUsage(**counts)


@router.delete("/{booking_type_id}", status_code=204)
def delete_booking_type(booking_type_id: int, db: Session = Depends(get_db)):
    """Hard delete, unguarded at any usage count. Referring rows keep existing and simply lose their
    label (the FK is ON DELETE SET NULL) — a type carries no rules and nothing branches on it, so
    there's nothing to protect. The frontend warns using /usage before calling this."""
    db_type = db.query(BookingType).filter(BookingType.id == booking_type_id).first()
    if not db_type:
        raise HTTPException(status_code=404, detail="Booking type not found")

    db.delete(db_type)
    db.commit()
