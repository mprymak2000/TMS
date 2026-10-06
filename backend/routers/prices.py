from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from billing import money
from models import BookingLink, Enrollment, Price
from schemas import PriceResponse, PriceSupersede

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/", response_model=list[PriceResponse])
def get_prices(include_archived: bool = Query(default=False), db: Session = Depends(get_db)):
    """The live catalog, which self-assembles from use — every rate and link price resolves through
    resolve_price, so anything in use is in here."""
    query = db.query(Price)
    if not include_archived:
        query = query.filter(Price.archived_at.is_(None))
    return query.order_by(Price.unit, Price.amount).all()


@router.get("/{price_id}/usage")
def get_price_usage(price_id: int, db: Session = Depends(get_db)):
    """Who's on this price. The frontend shows these counts before a supersede, since the enrollment
    count is how many clients are about to move."""
    if not db.query(Price).filter(Price.id == price_id).first():
        raise HTTPException(status_code=404, detail="Price not found")
    return {
        "enrollments": db.query(Enrollment).filter(Enrollment.rate_id == price_id).count(),
        "booking_links": db.query(BookingLink).filter(BookingLink.price_id == price_id).count(),
    }


@router.post("/{price_id}/supersede", response_model=PriceResponse, status_code=201)
def supersede_price(price_id: int, body: PriceSupersede, db: Session = Depends(get_db)):
    """Raise a price: new row, then move everyone currently on the old one to it.

    The old row is never edited, so every booking and invoice line that froze it still resolves to
    what was charged then. `exclude_enrollment_ids` is how you grandfather someone — left on the old
    row, which keeps working and just drops out of the catalog.
    """
    old = db.query(Price).filter(Price.id == price_id).first()
    if not old:
        raise HTTPException(status_code=404, detail="Price not found")
    if old.archived_at is not None:
        raise HTTPException(status_code=409, detail="This price has already been superseded")
    amount = money(body.amount)
    if amount == old.amount:
        raise HTTPException(status_code=409, detail="That is already the amount")

    # Unit carries over — changing it makes a different kind of price, not a new version of this one.
    existing = db.query(Price).filter(
        Price.amount == amount, Price.unit == old.unit, Price.archived_at.is_(None),
    ).first()
    new = existing or Price(amount=amount, unit=old.unit)
    if existing is None:
        db.add(new)
        db.flush()

    excluded = set(body.exclude_enrollment_ids or [])
    enrollments = db.query(Enrollment).filter(Enrollment.rate_id == price_id).all()
    for enrollment in enrollments:
        if enrollment.id not in excluded:
            enrollment.rate_id = new.id
    db.query(BookingLink).filter(BookingLink.price_id == price_id).update(
        {"price_id": new.id}, synchronize_session=False
    )

    # Keep the old row live only if someone was deliberately left on it.
    if not any(e.id in excluded for e in enrollments):
        old.archived_at = datetime.now(UTC)
    db.commit()
    db.refresh(new)
    return new
