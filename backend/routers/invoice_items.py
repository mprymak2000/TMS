from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import InvoiceItem
from schemas import InvoiceItemInput, InvoiceItemResponse

router = APIRouter(prefix="/invoice-items", tags=["invoice-items"])


@router.post("/", response_model=InvoiceItemResponse, status_code=201)
def create_item(body: InvoiceItemInput, db: Session = Depends(get_db)):
    """A charge with nothing to hang off — materials, a goodwill credit. Lands on whichever invoice
    for this payer is drawn up next; never retroactive onto a period already closed."""
    item = InvoiceItem(**body.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/", response_model=list[InvoiceItemResponse])
def list_items(
    payer_id: int | None = Query(default=None),
    pending: bool | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(InvoiceItem)
    if payer_id is not None:
        query = query.filter(InvoiceItem.payer_id == payer_id)
    if pending is True:
        query = query.filter(InvoiceItem.invoice_id.is_(None))
    elif pending is False:
        query = query.filter(InvoiceItem.invoice_id.isnot(None))
    return query.order_by(InvoiceItem.created.desc()).all()


@router.delete("/{item_id}", response_model=InvoiceItemResponse)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    """Pending only — once an invoice has swept it, it's that invoice's history."""
    item = db.query(InvoiceItem).filter(InvoiceItem.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.invoice_id is not None:
        raise HTTPException(status_code=409, detail="Already swept onto an invoice")
    response = InvoiceItemResponse.model_validate(item)
    db.delete(item)
    db.commit()
    return response
