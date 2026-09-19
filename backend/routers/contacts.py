from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from booking_utils import normalize_email
from database import get_db
from models import Booking, BookingSeries, Contact, ContactManager, Student
from schemas import ContactCreate, ContactListResponse, ContactPagedResponse, ContactResponse, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _free_email(db: Session, email: str | None, exclude_id: int | None = None) -> str | None:
    """Normalize the way resolve_payer does, and reject a collision. Returns what to store.

    exclude_id is the row being edited, so a PUT that leaves the email unchanged doesn't collide
    with itself."""
    if not email:
        return None
    normalized = normalize_email(email)
    query = db.query(Contact).filter(Contact.email == normalized)
    if exclude_id is not None:
        query = query.filter(Contact.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=409, detail="A contact with this email already exists")
    return normalized


SORT_COLUMNS = {
    "name": (Contact.first_name, Contact.last_name),
    "created": (Contact.created,),
    "email": (Contact.email,),
}


@router.get("/", response_model=ContactPagedResponse)
def get_contacts(
    search: str | None = Query(default=None),
    sort: str = Query(default="name"),
    direction: str = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Search, sort and paging happen here rather than in the browser: a large practice's roster is
    too big to ship whole, and the page needs a total it can't compute from one page."""
    if sort not in SORT_COLUMNS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(SORT_COLUMNS)}")
    if direction not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="direction must be 'asc' or 'desc'")

    query = db.query(Contact)
    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(or_(
            func.lower(Contact.first_name).like(term),
            func.lower(Contact.last_name).like(term),
            func.lower(Contact.email).like(term),
            func.lower(Contact.phone).like(term),
        ))

    total = query.count()
    ordering = [c.desc() if direction == "desc" else c.asc() for c in SORT_COLUMNS[sort]]
    # id last so a tie on the sort key can't drop or repeat a row across a page boundary.
    contacts = query.order_by(*ordering, Contact.id).offset((page - 1) * page_size).limit(page_size).all()

    # Two grouped counts scoped to this page, rather than a pair of queries per row. Bookings only:
    # a series always has materialized occurrences, so it can't give someone a role their bookings
    # don't already show.
    ids = [c.id for c in contacts]
    payer_counts = dict(
        db.query(Booking.payer_id, func.count()).filter(Booking.payer_id.in_(ids)).group_by(Booking.payer_id).all()
    ) if ids else {}
    attendee_counts = dict(
        db.query(Booking.attendee_id, func.count()).filter(Booking.attendee_id.in_(ids)).group_by(Booking.attendee_id).all()
    ) if ids else {}

    return ContactPagedResponse(
        items=[
            ContactListResponse(
                **ContactResponse.model_validate(c).model_dump(),
                created=c.created,
                bookings_as_payer=payer_counts.get(c.id, 0),
                bookings_as_attendee=attendee_counts.get(c.id, 0),
            )
            for c in contacts
        ],
        total=total,
    )


@router.get("/{contact_id:int}", response_model=ContactResponse)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.post("/", response_model=ContactResponse, status_code=201)
def create_contact(contact_in: ContactCreate, db: Session = Depends(get_db)):
    """Add someone who hasn't booked. Bookings create their own contacts via resolve_payer."""
    data = contact_in.model_dump()
    data["email"] = _free_email(db, contact_in.email)
    new_contact = Contact(**data)
    db.add(new_contact)
    db.commit()
    db.refresh(new_contact)
    return new_contact


@router.put("/{contact_id:int}", response_model=ContactResponse)
def update_contact(contact_id: int, contact_in: ContactUpdate, db: Session = Depends(get_db)):
    """The one place a person's details change. Bookings read through the FK, so a correction reaches
    their whole history rather than only the bookings made after it. A public form can't get here."""
    db_contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not db_contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    data = contact_in.model_dump()
    data["email"] = _free_email(db, contact_in.email, exclude_id=contact_id)
    for key, value in data.items():
        setattr(db_contact, key, value)
    db.commit()
    db.refresh(db_contact)
    return db_contact


@router.delete("/{contact_id:int}", response_model=ContactResponse)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    """Restrict, same shape as Tutor and Student. payer_id/attendee_id are NOT NULL so there's
    nothing to null out, and a booking with no person is meaningless.

    The delete that actually happens is a stray duplicate: repoint the bad booking's attendee first,
    which leaves that contact unreferenced and deletable here. A client who left isn't deleted, they
    get is_active=False on their enrollment."""
    db_contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not db_contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if db.query(Booking).filter(or_(Booking.payer_id == contact_id, Booking.attendee_id == contact_id)).first():
        raise HTTPException(status_code=409, detail="Cannot delete a contact with existing bookings")
    if db.query(BookingSeries).filter(or_(BookingSeries.payer_id == contact_id, BookingSeries.attendee_id == contact_id)).first():
        raise HTTPException(status_code=409, detail="Cannot delete a contact with existing series")
    if db.query(Student).filter(Student.contact_id == contact_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete an enrolled contact")
    # Relationship links are this contact's own rows, not a reference worth protecting. The FKs are
    # ON DELETE CASCADE; clearing them here keeps SQLite in step with Postgres.
    db.query(ContactManager).filter(
        or_(ContactManager.manager_id == contact_id, ContactManager.managed_id == contact_id)
    ).delete(synchronize_session=False)
    db.delete(db_contact)
    db.commit()
    return db_contact
