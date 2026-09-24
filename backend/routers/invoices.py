from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from billing import generate_invoices, refresh_invoice
from database import get_db, get_settings
from models import Invoice, InvoiceLine
from schemas import (
    InvoiceGenerate, InvoiceLineInput, InvoicePagedResponse, InvoiceResponse, InvoiceStatusUpdate,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])

# draft -> sent -> paid, and anything can be voided. No going back: an invoice that's been sent is a
# record of what the client was told, so a mistake is voided and reissued rather than edited back.
_NEXT_STATUS = {
    "draft": {"sent", "void"},
    "sent": {"paid", "void"},
    "paid": {"void"},
    "void": set(),
}


def _editable_invoice(public_id: str, db: Session) -> Invoice:
    """Drafts only. Once finalised, the lines are what the client was told they owe, so a mistake is
    voided and reissued rather than edited underneath them."""
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft invoice can be edited")
    return invoice


@router.get("/", response_model=InvoicePagedResponse)
def get_invoices(
    payer_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Page numbers rather than a cursor, same call as the client roster: a short list that shows a
    total and gets jumped around, not a feed scrolled to the end."""
    # The response reads payer_name and lines, so preload both — otherwise each invoice costs two
    # more queries at serialize time.
    query = db.query(Invoice).options(joinedload(Invoice.payer), joinedload(Invoice.lines))
    if payer_id is not None:
        query = query.filter(Invoice.payer_id == payer_id)
    if status is not None:
        query = query.filter(Invoice.status == status)

    total = query.count()
    invoices = (
        query.order_by(Invoice.period_start.desc(), Invoice.id.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return InvoicePagedResponse(items=invoices, total=total)


@router.get("/{public_id}", response_model=InvoiceResponse)
def get_invoice(public_id: str, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("/generate", response_model=list[InvoiceResponse], status_code=201)
def generate(
    body: InvoiceGenerate,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Draft invoices for a period, on demand. The monthly job calls the same code.

    Create only: an invoice that already exists is skipped whatever its status. Rebuilding one is
    /refresh, which you ask for rather than have happen to you.
    """
    return generate_invoices(db, body.period_start, body.period_end, settings)


@router.post("/{public_id}/refresh", response_model=InvoiceResponse)
def refresh(public_id: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    """Rebuild a draft against current data. Lines a human edited keep their amounts; the rest are
    recomputed. Drafts only — a finalised invoice is a record, not a calculation."""
    invoice = _editable_invoice(public_id, db)
    return refresh_invoice(db, invoice, settings)


@router.put("/{public_id}/status", response_model=InvoiceResponse)
def update_status(public_id: str, body: InvoiceStatusUpdate, db: Session = Depends(get_db)):
    """A verb subroute because it isn't a field write: each transition stamps its own timestamp, and
    only some are legal. Nothing collects payment, so `paid` is marked by hand when the money lands."""
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if body.status not in _NEXT_STATUS[invoice.status]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move an invoice from {invoice.status} to {body.status}",
        )

    invoice.status = body.status
    if body.status == "sent":
        invoice.sent_at = datetime.now(UTC)
    elif body.status == "paid":
        invoice.paid_at = datetime.now(UTC)
    db.commit()
    db.refresh(invoice)
    return invoice


# ── lines ────────────────────────────────────────────────────────────────────
def _retotal(db: Session, invoice: Invoice) -> Invoice:
    db.flush()
    invoice.total = round(sum(line.amount for line in invoice.lines), 2)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.post("/{public_id}/lines", response_model=InvoiceResponse, status_code=201)
def add_line(public_id: str, body: InvoiceLineInput, db: Session = Depends(get_db)):
    """A charge with no booking behind it — a materials fee, a late-cancellation penalty."""
    invoice = _editable_invoice(public_id, db)
    invoice.lines.append(InvoiceLine(description=body.description, amount=body.amount))
    return _retotal(db, invoice)


@router.put("/{public_id}/lines/{line_id}", response_model=InvoiceResponse)
def update_line(public_id: str, line_id: int, body: InvoiceLineInput, db: Session = Depends(get_db)):
    """Correct a generated line. Its source FK stays put, so the line still says where it came from.

    The first edit saves the generated amount into `computed`, which is both the record of what the
    rules produced and the signal that a refresh must not overwrite this line."""
    invoice = _editable_invoice(public_id, db)
    line = next((l for l in invoice.lines if l.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=404, detail="Line not found on this invoice")
    if line.computed is None:
        line.computed = line.amount   # later edits keep the original, not the previous edit
    line.description = body.description
    line.amount = body.amount
    return _retotal(db, invoice)


@router.delete("/{public_id}/lines/{line_id}", response_model=InvoiceResponse)
def delete_line(public_id: str, line_id: int, db: Session = Depends(get_db)):
    """Note a regenerate puts it back — the generator rebuilds a draft from scratch."""
    invoice = _editable_invoice(public_id, db)
    line = next((l for l in invoice.lines if l.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=404, detail="Line not found on this invoice")
    invoice.lines.remove(line)
    return _retotal(db, invoice)


@router.delete("/{public_id}", response_model=InvoiceResponse)
def delete_invoice(public_id: str, db: Session = Depends(get_db)):
    """Drafts only — a draft is just a calculation nobody has seen. Once it's sent it's void, never
    deleted, so the numbering has no holes in it."""
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be deleted; void it instead")
    response = InvoiceResponse.model_validate(invoice)
    db.delete(invoice)
    db.commit()
    return response
