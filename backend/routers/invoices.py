from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from billing import (
    draft_invoice_for_payer, generate_invoices, line_charged_amount, money, release_line,
    set_invoice_sources,
)
from database import get_db, get_settings
from models import Invoice
from schemas import (
    InvoiceCreate, InvoiceGenerate, InvoiceLineAdjustmentInput, InvoicePagedResponse,
    InvoicePaymentUpdate, InvoiceResponse, InvoiceSourcesUpdate, InvoiceStatusUpdate,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])

# draft -> finalized, and anything can be voided. No going back: a finalized invoice is a record of
# what the client was told, so a mistake is voided and reissued rather than edited back. Payment
# isn't a status transition — see update_payment.
_NEXT_STATUS = {
    "draft": {"finalized", "void"},
    "finalized": {"void"},
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


def _allocate_invoice_number(db: Session, year: int) -> str:
    """Next INV-{year}-{seq:04d}. Locked so two finalizes at once can't collide."""
    prefix = f"INV-{year}-"
    highest = (
        db.query(Invoice)
        .filter(Invoice.number.like(f"{prefix}%"))
        .order_by(Invoice.number.desc())
        .with_for_update()
        .first()
    )
    seq = int(highest.number.removeprefix(prefix)) + 1 if highest else 1
    return f"{prefix}{seq:04d}"


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
    """Draft invoices for a period, on demand — the same sweep the monthly automation job calls.
    payer_ids narrows to a specific set; omitted, everyone with outstanding activity in the period.

    Create only: a payer who already has an invoice for this period is skipped. Wrong line? Delete
    the draft and generate again — there's no rebuild-in-place.
    """
    return generate_invoices(db, body.period_start, body.period_end, settings, body.payer_ids)


@router.post("/", response_model=InvoiceResponse, status_code=201)
def create(
    body: InvoiceCreate,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """One invoice for one payer — the manual/ad-hoc counterpart to /generate. No period and no
    explicit ids sweeps everything currently outstanding for them."""
    invoice = draft_invoice_for_payer(
        db, body.payer_id, settings, body.period_start, body.period_end, body.booking_ids, body.item_ids,
    )
    if invoice is None:
        raise HTTPException(
            status_code=409,
            detail="Nothing to invoice — either nothing is outstanding, or an invoice already covers an overlapping period",
        )
    return invoice


@router.put("/{public_id}/status", response_model=InvoiceResponse)
def update_status(public_id: str, body: InvoiceStatusUpdate, db: Session = Depends(get_db)):
    """A verb subroute because it isn't a field write: only some transitions are legal, and
    finalizing stamps `sent_at`. Payment is a separate axis — see update_payment."""
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if body.status not in _NEXT_STATUS[invoice.status]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move an invoice from {invoice.status} to {body.status}",
        )

    invoice.status = body.status
    if body.status == "finalized":
        now = datetime.now(UTC)
        invoice.sent_at = now
        invoice.number = _allocate_invoice_number(db, now.year)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.put("/{public_id}/payment", response_model=InvoiceResponse)
def update_payment(public_id: str, body: InvoicePaymentUpdate, db: Session = Depends(get_db)):
    """Money received, independent of the document's own state. A draft has nothing to be paid for
    yet, so this requires the invoice to be finalized first; voided is left reachable, since
    correcting a payment record on a voided invoice is still a legitimate admin action."""
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "draft":
        raise HTTPException(status_code=409, detail="A draft invoice has nothing to be paid")

    invoice.payment_status = body.payment_status
    invoice.paid_at = datetime.now(UTC) if body.payment_status == "paid" else None
    db.commit()
    db.refresh(invoice)
    return invoice


# ── lines ────────────────────────────────────────────────────────────────────
@router.put("/{public_id}/lines", response_model=InvoiceResponse)
def update_sources(public_id: str, body: InvoiceSourcesUpdate, db: Session = Depends(get_db), settings=Depends(get_settings)):
    """Replace the draft's selectable membership in one call — the admin's pick/unpick UI sends the
    full set it ended up with, not a delta. Existing adjustments on lines that stay selected are
    preserved; the monthly line is untouched, since it isn't part of this set."""
    invoice = _editable_invoice(public_id, db)
    try:
        set_invoice_sources(db, invoice, settings, body.booking_ids, body.item_ids)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.commit()
    db.refresh(invoice)
    return invoice


@router.put("/{public_id}/lines/{line_id}", response_model=InvoiceResponse)
def update_line(public_id: str, line_id: int, body: InvoiceLineAdjustmentInput, db: Session = Depends(get_db)):
    """Set or clear a line's correction. Writes straight onto the line's own adjustment_amount/
    adjustment_percent — amount itself is never touched, so this is always undoable by sending both
    fields null."""
    invoice = _editable_invoice(public_id, db)
    line = next((l for l in invoice.lines if l.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=404, detail="Line not found on this invoice")

    # Decimal, not the float Pydantic parsed — the arithmetic below mixes it with amount.
    line.adjustment_amount = None if body.adjustment_amount is None else money(body.adjustment_amount)
    line.adjustment_percent = None if body.adjustment_percent is None else money(body.adjustment_percent)
    db.flush()
    invoice.total = round(sum(line_charged_amount(l) for l in invoice.lines), 2)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.delete("/{public_id}/lines/{line_id}", response_model=InvoiceResponse)
def delete_line(public_id: str, line_id: int, db: Session = Depends(get_db)):
    """Remove a line and release whatever it claimed, so the source is billable again on the next
    sweep."""
    invoice = _editable_invoice(public_id, db)
    line = next((l for l in invoice.lines if l.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=404, detail="Line not found on this invoice")

    release_line(db, invoice, line)
    db.commit()
    db.refresh(invoice)
    return invoice


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
