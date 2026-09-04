from datetime import datetime, UTC
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import BookingLink, BookingLinkAvailability, Booking, BookingSeries
from schemas import BookingLinkCreate, BookingLinkUpdate, BookingLinkResponse
from booking_utils import active_series_filter

router = APIRouter(prefix="/booking_links", tags=["booking_links"])


def _slug_taken(db: Session, slug: str, ignore_link_id: int | None = None) -> bool:
    """Local helper for the create/update slug check — nothing else calls it.

    Uniqueness is scoped to links that still route (active and paused): archiving is what releases
    the name for reuse. `ignore_link_id` is only passed by update, so a link keeping its own slug
    doesn't collide with itself.
    """
    q = db.query(BookingLink).filter(BookingLink.slug == slug, BookingLink.status != "archived")
    if ignore_link_id is not None:
        q = q.filter(BookingLink.id != ignore_link_id)
    return db.query(q.exists()).scalar()


@router.get("/", response_model=list[BookingLinkResponse])
def get_booking_links(include_archived: bool = False, db: Session = Depends(get_db)):
    q = db.query(BookingLink)
    if not include_archived:
        q = q.filter(BookingLink.status != "archived")  # paused links stay listed — they're coming back
    return q.all()


@router.get("/slug/{slug}", response_model=BookingLinkResponse)
def get_booking_link_by_slug(slug: str, db: Session = Depends(get_db)):
    """Public lookup for the booking page.

    Resolves paused links too, so the page can say *why* it isn't bookable rather than 404ing — the
    actual block is create_booking's require_link_bookable. Archived is excluded outright: its slug
    is released for reuse, so matching it could resolve one value to two links.
    """
    db_link = db.query(BookingLink).filter(
        BookingLink.slug == slug, BookingLink.status != "archived"
    ).first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    return db_link


@router.get("/{booking_link_id:int}", response_model=BookingLinkResponse)
def get_booking_link(booking_link_id: int, include_archived: bool = False, db: Session = Depends(get_db)):
    """Admin lookup by id. Opt in with `include_archived` to reach a retired link."""
    q = db.query(BookingLink).filter(BookingLink.id == booking_link_id)
    if not include_archived:
        q = q.filter(BookingLink.status != "archived")
    db_link = q.first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    return db_link


@router.post("/", response_model=BookingLinkResponse, status_code=201)
def create_booking_link(link_in: BookingLinkCreate, db: Session = Depends(get_db)):
    if _slug_taken(db, link_in.slug):
        raise HTTPException(status_code=409, detail="A booking link already uses this URL")
    if link_in.min_duration_minutes is not None and link_in.max_duration_minutes is None:
        raise HTTPException(status_code=400, detail="max_duration_minutes is required when min_duration_minutes is set")
    if link_in.min_duration_minutes is not None and link_in.min_duration_minutes >= link_in.max_duration_minutes:
        raise HTTPException(status_code=400, detail="min_duration_minutes must be less than max_duration_minutes")


    db_link = BookingLink(**link_in.model_dump(exclude={"availability"}))
    db.add(db_link)
    db.flush() # to get booking link id for availability entries
    for tutor_schedule in link_in.availability:
        booking_link_availability = BookingLinkAvailability(
            booking_link_id=db_link.id,
            tutor_id=tutor_schedule.tutor_id,
            schedule_id=tutor_schedule.schedule_id,
        )
        db.add(booking_link_availability)
    db.commit()
    db.refresh(db_link)
    return db_link


@router.put("/{booking_link_id:int}", response_model=BookingLinkResponse)
def update_booking_link(booking_link_id: int, link_in: BookingLinkUpdate, db: Session = Depends(get_db)):
    db_link = db.query(BookingLink).filter(BookingLink.id == booking_link_id).first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    # An archived link's calendar rules govern nothing, so there's no edit worth making.
    if db_link.status == "archived":
        raise HTTPException(status_code=403, detail="Archived booking links are read-only")

    if _slug_taken(db, link_in.slug, ignore_link_id=booking_link_id):
        raise HTTPException(status_code=409, detail="A booking link already uses this URL")
    if link_in.min_duration_minutes is not None and link_in.max_duration_minutes is None:
        raise HTTPException(status_code=400, detail="max_duration_minutes is required when min_duration_minutes is set")
    if link_in.min_duration_minutes is not None and link_in.min_duration_minutes >= link_in.max_duration_minutes:
        raise HTTPException(status_code=400, detail="min_duration_minutes must be less than max_duration_minutes")


    for field, value in link_in.model_dump(exclude={"availability"}).items():
        setattr(db_link, field, value)
    db.query(BookingLinkAvailability).filter(BookingLinkAvailability.booking_link_id == booking_link_id).delete()
    for tutor_schedule in link_in.availability:
        booking_link_availability = BookingLinkAvailability(
            booking_link_id=db_link.id,
            tutor_id=tutor_schedule.tutor_id,
            schedule_id=tutor_schedule.schedule_id,
        )
        db.add(booking_link_availability)
    db.commit()
    db.refresh(db_link)
    return db_link


@router.delete("/{booking_link_id:int}", response_model=BookingLinkResponse)
def archive_booking_link(booking_link_id: int, db: Session = Depends(get_db)):
    """Archive is the only delete — there is no hard delete at any child count.

    The row lives forever so `booking_link_id` never dangles, which is what keeps a link's bookings
    groupable and, more importantly, bulk-reassignable to a live link. Permanent in behavior: no
    restore. A booking stranded on an archived link is rescued by reassigning *the booking*, never
    by reviving the link.
    """
    db_link = db.query(BookingLink).options(joinedload(BookingLink.availability)).filter(
        BookingLink.id == booking_link_id
    ).first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    if db_link.status == "archived":
        raise HTTPException(status_code=409, detail="Booking link is already archived")

    db_link.status = "archived"
    db_link.archived_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_link)
    return db_link


@router.post("/{booking_link_id:int}/pause", response_model=BookingLinkResponse)
def pause_booking_link(booking_link_id: int, db: Session = Depends(get_db)):
    """Stop taking new bookings, reversibly. Unlike archive, calendar rules stay live and editable,
    so existing bookings can still be rescheduled and series keep running untouched."""
    db_link = db.query(BookingLink).filter(BookingLink.id == booking_link_id).first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    if db_link.status == "archived":
        raise HTTPException(status_code=400, detail="Archived booking links cannot be paused")
    db_link.status = "paused"
    db.commit()
    db.refresh(db_link)
    return db_link


@router.post("/{booking_link_id:int}/resume", response_model=BookingLinkResponse)
def resume_booking_link(booking_link_id: int, db: Session = Depends(get_db)):
    """Undo a pause. Only paused links can resume — archive is terminal."""
    db_link = db.query(BookingLink).filter(BookingLink.id == booking_link_id).first()
    if not db_link:
        raise HTTPException(status_code=404, detail="Booking link not found")
    if db_link.status != "paused":
        raise HTTPException(status_code=400, detail="Only paused booking links can be resumed")
    db_link.status = "active"
    db.commit()
    db.refresh(db_link)
    return db_link


@router.get("/{booking_link_id:int}/impact")
def get_archive_impact(booking_link_id: int, include_archived: bool = False, db: Session = Depends(get_db)):
    """What archiving this link would cost — drives the admin confirm step.

    Upcoming bookings lose customer self-reschedule until they're reassigned to a live link.
    Series are unaffected: they generate from their own row, not the link.
    """
    q = db.query(BookingLink).filter(BookingLink.id == booking_link_id)
    if not include_archived:
        q = q.filter(BookingLink.status != "archived")
    if not q.first():
        raise HTTPException(status_code=404, detail="Booking link not found")

    now = datetime.now(UTC)
    upcoming = db.query(Booking).filter(
        Booking.booking_link_id == booking_link_id,
        Booking.status == "confirmed",
        Booking.start >= now,
    ).count()
    # A series is active unless cancelled/rescheduled or naturally expired — status alone doesn't
    # say (an active series has status IS NULL). Reuse the shared filter rather than re-deriving it.
    series = db.query(BookingSeries).filter(
        BookingSeries.booking_link_id == booking_link_id,
        active_series_filter(now.date()),
    ).count()
    return {"upcoming_bookings": upcoming, "active_series": series}