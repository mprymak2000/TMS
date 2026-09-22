from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from booking_utils import normalize_email
from database import get_db
from models import Booking, BookingSeries, Contact, ContactManager, Enrollment, Lesson
from schemas import (
    ContactCreate, ContactListResponse, ContactPagedResponse, ContactRelationshipsResponse,
    ContactResponse, ContactUpdate, EnrollmentInput, EnrollmentResponse,
)

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


def _list_row(contact: Contact) -> ContactListResponse:
    return ContactListResponse(
        **ContactResponse.model_validate(contact).model_dump(),
        created=contact.created,
        enrollment=EnrollmentResponse.model_validate(contact.enrollment) if contact.enrollment else None,
    )


@router.get("/", response_model=ContactPagedResponse)
def get_contacts(
    search: str | None = Query(default=None),
    enrolled: bool = Query(default=False),
    manages: bool = Query(default=False),
    sort: str = Query(default="name"),
    direction: str = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Search, sort and paging happen here rather than in the browser: a large practice's roster is
    too big to ship whole, and the page needs a total it can't compute from one page.

    Two independent booleans rather than one enum, so they combine — a payer can also be enrolled.
    `enrolled` means "has an enrollment", inactive included: someone who left still has history worth
    finding. It's not "has ever attended", which is a different set. `manages` comes from
    contact_managers, not booking counts — the standing relationship, not having transacted."""
    if sort not in SORT_COLUMNS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(SORT_COLUMNS)}")
    if direction not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="direction must be 'asc' or 'desc'")

    query = db.query(Contact)
    if enrolled:
        query = query.filter(Contact.enrollment.has())
    if manages:
        query = query.filter(
            db.query(ContactManager).filter(ContactManager.manager_id == Contact.id).exists()
        )
    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(or_(
            func.lower(Contact.first_name).like(term),
            func.lower(Contact.last_name).like(term),
            # The joined name too, so "marcus chen" matches — neither column contains that string on
            # its own, and typing a full name is the obvious thing to try.
            func.lower(Contact.first_name + " " + Contact.last_name).like(term),
            func.lower(Contact.email).like(term),
            func.lower(Contact.phone).like(term),
        ))

    total = query.count()
    ordering = [c.desc() if direction == "desc" else c.asc() for c in SORT_COLUMNS[sort]]
    # id last so a tie on the sort key can't drop or repeat a row across a page boundary.
    contacts = query.order_by(*ordering, Contact.id).offset((page - 1) * page_size).limit(page_size).all()

    return ContactPagedResponse(items=[_list_row(c) for c in contacts], total=total)


@router.get("/{contact_id:int}", response_model=ContactListResponse)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    """Same shape as a roster row, so the panel can open on someone who isn't on the current page."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return _list_row(contact)


@router.get("/{contact_id:int}/relationships", response_model=ContactRelationshipsResponse)
def get_contact_relationships(contact_id: int, db: Session = Depends(get_db)):
    """Who this person books for, and who books for them. Read-only: the rows are written by
    bookings (resolve_attendee), not by hand."""
    if not db.query(Contact).filter(Contact.id == contact_id).first():
        raise HTTPException(status_code=404, detail="Contact not found")
    manages = (
        db.query(Contact).join(ContactManager, ContactManager.managed_id == Contact.id)
        .filter(ContactManager.manager_id == contact_id)
        .order_by(Contact.first_name, Contact.last_name).all()
    )
    managed_by = (
        db.query(Contact).join(ContactManager, ContactManager.manager_id == Contact.id)
        .filter(ContactManager.managed_id == contact_id)
        .order_by(Contact.first_name, Contact.last_name).all()
    )
    return ContactRelationshipsResponse(manages=manages, managed_by=managed_by)


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


@router.put("/{contact_id:int}", response_model=ContactListResponse)
def update_contact(contact_id: int, contact_in: ContactUpdate, db: Session = Depends(get_db)):
    """The one place a person's details change. Bookings read through the FK, so a correction reaches
    their whole history rather than only the bookings made after it. A public form can't get here.

    Carries the enrollment too when present, in the same commit — the client panel saves identity and
    rate together, and two calls could leave a renamed client on the old rate if the second failed."""
    db_contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not db_contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    data = contact_in.model_dump(exclude={"enrollment"})
    data["email"] = _free_email(db, contact_in.email, exclude_id=contact_id)
    for key, value in data.items():
        setattr(db_contact, key, value)
    if contact_in.enrollment is not None:
        _upsert_enrollment(db, contact_id, contact_in.enrollment)
    db.commit()
    db.refresh(db_contact)
    return _list_row(db_contact)


@router.delete("/{contact_id:int}", response_model=ContactResponse)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    """A booking names a payer (contact) and an attendee (contact), and can't exist without either,
    so we refuse to delete anyone a booking still points at.

    The enrollment gets no guard — it's part of the contact and goes with it. Anything that depends
    on the enrollment still blocks the delete, since that failure rolls back the whole thing.

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
    if db.query(Lesson).filter(Lesson.enrollment_id == contact_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete a contact with recorded lessons")
    # Being enrolled is deliberately not a guard — only lessons and bookings are. Enrolled contact,
    # no lessons or bookings:
    #   no guard -> db deletes the enrollment and the contact
    #   guard    -> 409, user deletes the enrollment by hand, retries, same result
    # The guard buys a step and nothing else.
    # Their dependents would be left with nobody to book or bill for them. The booking guards miss
    # this: a monthly enrollment bills on a schedule, with no booking involved.
    if db.query(ContactManager).filter(ContactManager.manager_id == contact_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete a contact who manages others")
    # Only "someone manages them" rows can remain. Cleared explicitly so SQLite matches Postgres.
    db.query(ContactManager).filter(ContactManager.managed_id == contact_id).delete(synchronize_session=False)
    db.delete(db_contact)
    db.commit()
    return db_contact


# ── enrollment ───────────────────────────────────────────────────────────────
# A sub-resource, not a table of its own: an enrollment extends one contact, is never reassigned,
# and its id IS that contact's id — so the URL names it before the row exists.


def _upsert_enrollment(db: Session, contact_id: int, enrollment_in: EnrollmentInput) -> tuple[Enrollment, bool]:
    """Write only — no commit, so a caller can fold it into a larger transaction. Returns (row, created)."""
    db_enrollment = db.query(Enrollment).filter(Enrollment.id == contact_id).first()
    if db_enrollment is None:
        db_enrollment = Enrollment(id=contact_id, **enrollment_in.model_dump())
        db.add(db_enrollment)
        return db_enrollment, True
    for key, value in enrollment_in.model_dump().items():
        setattr(db_enrollment, key, value)
    return db_enrollment, False


@router.put("/{contact_id:int}/enrollment", response_model=EnrollmentResponse)
def upsert_enrollment(contact_id: int, enrollment_in: EnrollmentInput, response: Response, db: Session = Depends(get_db)):
    """One call for enrolling and for editing. There can only ever be one, and the address doesn't
    depend on whether it exists, so PUT covers both — 201 when it creates, 200 when it replaces."""
    if not db.query(Contact).filter(Contact.id == contact_id).first():
        raise HTTPException(status_code=404, detail="Contact not found")
    db_enrollment, created = _upsert_enrollment(db, contact_id, enrollment_in)
    if created:
        response.status_code = 201
    db.commit()
    db.refresh(db_enrollment)
    return db_enrollment


@router.delete("/{contact_id:int}/enrollment", response_model=EnrollmentResponse)
def delete_enrollment(contact_id: int, db: Session = Depends(get_db)):
    """For mistakes only — it says "this person was never a student", which is usually false.

    The row holds their rate, start date, grade and birthday. Someone who stopped coming gets
    is_active=False, which keeps all of it; deleting throws it away.

    The contact is untouched either way — they just have no negotiated rate, so bookings fall back
    to the link's price. Bookings never reference the enrollment at all, only lessons do, which is
    why lessons are the only thing that can block this."""
    db_enrollment = db.query(Enrollment).filter(Enrollment.id == contact_id).first()
    if not db_enrollment:
        raise HTTPException(status_code=404, detail="Contact is not enrolled")
    if db.query(Lesson).filter(Lesson.enrollment_id == contact_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete an enrollment with recorded lessons")
    response = EnrollmentResponse.model_validate(db_enrollment)
    db.delete(db_enrollment)
    db.commit()
    return response
