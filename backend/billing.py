"""Turning bookings and charges into invoices.

A line is generated once and frozen, with the underlying source objects locked, so there is no drift
as long as an invoice is drafted. To unlock editing, remove line in question or delete the draft and 
regenerate draft. Corrections to an invoice's line live as separate columns on the line itself. They 
don't affect the source object. 

The rule, per booking:

    skip if status not in (confirmed, charged)   a plain cancel/reschedule doesn't bill; a no-show
                                                  does; a cancelled booking with a late-cancel charge
                                                  still bills for that charge alone
    skip if monthly client AND the series        what their plan already pays for; a second series
           is covered by their enrollment        bills
    amount = booking.charge                      admin override, including 0 for a freebie
          ?? booking.rate.amount by unit         frozen off the enrollment at creation
          ?? booking.price.amount by unit        link's price, frozen at creation; a monthly
                                                  client's extra, or no enrollment at all

Plus one line per active per_month enrollment in the period, generated fresh from enrollment.rate
each sweep, and one per swept InvoiceItem.

Lifecycle: bookings and InvoiceItems share one claim-based mechanism (invoice_id, nullable, set once
swept). A booking's eligibility is additionally bounded by a period if the invoice has one; an
InvoiceItem has no date at all, eligible the moment it's pending, period or not. One function
(_lines_for_payer) builds lines for both, the only branch being whether a period applies.
"""
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from models import Booking, Enrollment, Invoice, InvoiceItem, InvoiceLine, Price


def money(value) -> Decimal:
    """Money columns are Decimal; quantities (hours, a form amount) arrive as float. Via str(), so
    1.5 doesn't arrive as 1.4999..."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _hours(booking: Booking) -> float:
    # SQLite hands back naive datetimes; everything here is stored UTC. Same guard as booking_utils.
    start = booking.start if booking.start.tzinfo else booking.start.replace(tzinfo=UTC)
    end = booking.end if booking.end.tzinfo else booking.end.replace(tzinfo=UTC)
    return (end - start).total_seconds() / 3600


def resolve_price(db: Session, amount: float, unit: str) -> Price:
    """The live Price row for this amount+unit, created if it doesn't exist yet. Dedup is what lets
    clients on the same price share a row, so one UPDATE can move them all."""
    amount = money(amount)
    price = db.query(Price).filter(
        Price.amount == amount, Price.unit == unit, Price.archived_at.is_(None),
    ).first()
    if price is None:
        price = Price(amount=amount, unit=unit)
        db.add(price)
        db.flush()
    return price


def _by_unit(price: Price, booking: Booking) -> Decimal:
    return price.amount * money(_hours(booking)) if price.unit == "per_hour" else price.amount


def _price_amount(booking: Booking) -> Decimal | None:
    """The link's price, frozen onto the booking at creation."""
    return None if booking.price is None else _by_unit(booking.price, booking)


def _line_amount(booking: Booking, enrollment: Enrollment | None) -> Decimal | None:
    """What this one booking bills. None means no line — covered by a subscription, or unpriced.

    Both pointers were frozen at creation, so nothing here reads a live rate or link price."""
    if booking.charge is not None:
        return booking.charge

    if booking.rate is not None:
        if booking.rate.unit in ("per_hour", "per_session"):
            return _by_unit(booking.rate, booking)
        # Monthly plan covering this series: no line. A second series (extras on top of the usual
        # schedule) isn't covered, so it falls through and bills at the link's price. Coverage is
        # checked live on purpose — it's who's paying for what, not a price.
        if booking.rate.unit == "per_month" and booking.series is not None and enrollment is not None \
                and booking.series.covered_by_enrollment_id == enrollment.id:
            return None

    return _price_amount(booking)


def line_charged_amount(line: InvoiceLine) -> Decimal:
    """What a line actually costs — amount plus whichever adjustment is set, never both."""
    if line.adjustment_amount is not None:
        return line.amount + line.adjustment_amount
    if line.adjustment_percent is not None:
        return line.amount * (1 - line.adjustment_percent / 100)
    return line.amount


def _period_bounds(period_start: date | None, period_end: date | None, settings) -> tuple[datetime | None, datetime | None]:
    """Half-open [start, end) in business time, as UTC — bookings are stored UTC. None in, none
    out: an ad-hoc call has no window to convert."""
    if period_start is None:
        return None, None
    tz = ZoneInfo(settings.business_timezone)
    return (
        datetime.combine(period_start, time.min, tzinfo=tz).astimezone(UTC),
        datetime.combine(period_end, time.min, tzinfo=tz).astimezone(UTC),
    )


def _subscriptions(db: Session, period_start: date, period_end: date):
    """Monthly stints overlapping the period. Started in June isn't billed for May, and someone who
    left in April isn't billed for May either."""
    return db.query(Enrollment).join(Price, Enrollment.rate_id == Price.id).filter(
        Price.unit == "per_month",
        Enrollment.started_on < period_end,
        or_(Enrollment.ended_on.is_(None), Enrollment.ended_on > period_start),
    )


def payers_with_activity(db: Session, period_start: date, period_end: date, settings) -> set[int]:
    """Anyone the period could owe something for: bookings they paid for, or a monthly plan."""
    start_utc, end_utc = _period_bounds(period_start, period_end, settings)
    payers = {
        payer_id for (payer_id,) in db.query(Booking.payer_id).filter(
            or_(Booking.status == "confirmed", Booking.charge.isnot(None)),
            Booking.start >= start_utc, Booking.start < end_utc,
        ).distinct()
    }
    for enrollment in _subscriptions(db, period_start, period_end):
        # client is attendee, either bill to them or a payer if they have one under their enrollment.
        payers.add(enrollment.payer_id or enrollment.contact_id)
    return payers


def _overlapping_invoice_exists(db: Session, payer_id: int, period_start: date, period_end: date) -> bool:
    """A new period invoice can't be created if its range overlaps an existing non-void one for this
    payer — the monthly fee is derived fresh each sweep and carries no claim of its own, so this is
    the only thing stopping it from being billed twice across two overlapping periods. Catching up on
    something missed is always the ad-hoc path (explicit booking_ids/item_ids, no period), never a
    second overlapping period invoice."""
    return db.query(Invoice).filter(
        Invoice.payer_id == payer_id,
        Invoice.status != "void",
        Invoice.period_start.isnot(None),
        Invoice.period_start < period_end,
        Invoice.period_end > period_start,
    ).first() is not None


def _booking_line(booking: Booking, tz: ZoneInfo) -> InvoiceLine | None:
    amount = _line_amount(booking, booking.attendee.current_enrollment)
    if amount is None:
        return None
    start = booking.start if booking.start.tzinfo else booking.start.replace(tzinfo=UTC)
    attendee = booking.attendee
    return InvoiceLine(
        booking_id=booking.id,
        description=f"{attendee.first_name} {attendee.last_name} — session {start.astimezone(tz):%b %-d}",
        amount=amount,
    )


def _item_line(item: InvoiceItem) -> InvoiceLine:
    return InvoiceLine(invoice_item_id=item.id, description=item.description, amount=item.amount)


def _lock_payer(db: Session, payer_id: int) -> None:
    """Serialize generation for one payer. Every guard here is check-then-write — the claim, the
    overlap check — so two concurrent sweeps can both pass and both bill. Transaction-scoped, so it
    releases on commit or rollback with nothing to leak or time out.

    Postgres only; SQLite has no advisory locks and the tests are single-threaded.
    """
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"invoice:{payer_id}"})


def _lines_for_payer(
        db: Session,
        payer_id: int,
        settings,
        period_start: date | None = None,
        period_end: date | None = None,
        booking_ids: list[int] | None = None,
        item_ids: list[int] | None = None,
    ) -> tuple[list[InvoiceLine], list[Booking], list[InvoiceItem]]:
    """Everything one payer owes, swept once, claimed once. Bookings are additionally bounded by a
    period if one is given; InvoiceItems never are. The monthly fee is generated fresh each sweep,
    not claimed itself — protected from double-billing by _overlapping_invoice_exists instead.
    Explicit booking_ids/item_ids narrow within whatever period applies, never escape it. Returns
    the lines plus the claimed bookings/items so the caller can stamp invoice_id on them atomically.
    """
    tz = ZoneInfo(settings.business_timezone)
    start_utc, end_utc = _period_bounds(period_start, period_end, settings)
    lines: list[InvoiceLine] = []

    # bookings that STARTED inside the billing period (if a session started in period 1 and ended in
    # period 2, it's under period 1). Not gated to status == "confirmed" alone — a cancelled booking
    # with a late-cancellation charge still owes that charge. invoice_id IS NULL: claimed bookings
    # are already billed, never swept again.
    booking_query = db.query(Booking).filter(
        Booking.payer_id == payer_id,
        or_(Booking.status == "confirmed", Booking.charge.isnot(None)),
        Booking.invoice_id.is_(None),
    )
    if start_utc is not None:
        booking_query = booking_query.filter(Booking.start >= start_utc, Booking.start < end_utc)
    if booking_ids is not None:
        booking_query = booking_query.filter(Booking.id.in_(booking_ids))
    bookings = booking_query.all()

    for booking in bookings:
        line = _booking_line(booking, tz)
        if line is not None:
            lines.append(line)
    claimed_bookings = [b for b in bookings if any(l.booking_id == b.id for l in lines)]

    # Monthly fee — only within a real period, generated directly from enrollment.rate, no claim.
    if period_start is not None:
        for enrollment in _subscriptions(db, period_start, period_end):
            if (enrollment.payer_id or enrollment.contact_id) != payer_id:
                continue
            client = enrollment.contact
            lines.append(InvoiceLine(
                enrollment_id=enrollment.id,
                description=f"{client.first_name} {client.last_name} — monthly plan",
                amount=enrollment.rate.amount,
            ))

    # Pending items — dateless, narrowed by item_ids if given.
    item_query = db.query(InvoiceItem).filter(
        InvoiceItem.payer_id == payer_id,
        InvoiceItem.invoice_id.is_(None),
    )
    if item_ids is not None:
        item_query = item_query.filter(InvoiceItem.id.in_(item_ids))
    items = item_query.all()
    lines.extend(_item_line(item) for item in items)

    return lines, claimed_bookings, items


def draft_invoice_for_payer(
        db: Session,
        payer_id: int,
        settings,
        period_start: date | None = None,
        period_end: date | None = None,
        booking_ids: list[int] | None = None,
        item_ids: list[int] | None = None,
    ) -> Invoice | None:
    """Create one invoice for one payer. With no period and no explicit ids, sweeps everything
    currently outstanding for them, whenever it's from. A period, if given, is checked against
    _overlapping_invoice_exists first — the only guard this needs, since the claim already protects
    every booking and item from being billed twice regardless of period."""
    _lock_payer(db, payer_id)
    if period_start is not None and _overlapping_invoice_exists(db, payer_id, period_start, period_end):
        return None
    lines, bookings, items = _lines_for_payer(
        db, payer_id, settings, period_start, period_end, booking_ids, item_ids,
    )
    if not lines:
        return None
    invoice = Invoice(payer_id=payer_id, period_start=period_start, period_end=period_end, lines=lines)
    invoice.total = round(sum(line_charged_amount(line) for line in lines), 2)
    db.add(invoice)
    for booking in bookings:
        booking.invoice = invoice   # claims it, in the same commit as the lines it produced
    for item in items:
        item.invoice = invoice
    db.commit()
    db.refresh(invoice)   # pick up public_id / created / status defaults
    return invoice


def release_line(db: Session, invoice: Invoice, line: InvoiceLine) -> None:
    """Un-claims whatever the line billed (if anything — a monthly line claims nothing) and drops
    the line itself. Shared by both escape hatches: deleting the line from the invoice side, and an
    override on a blocked source-side edit."""
    if line.booking_id is not None:
        db.query(Booking).filter(Booking.id == line.booking_id).update({"invoice_id": None})
    elif line.invoice_item_id is not None:
        db.query(InvoiceItem).filter(InvoiceItem.id == line.invoice_item_id).update({"invoice_id": None})
    db.delete(line)
    db.flush()
    invoice.total = round(sum(line_charged_amount(l) for l in invoice.lines if l.id != line.id), 2)


class ClaimedByDraft(Exception):
    """Raised instead of silently allowing an edit that a draft invoice already froze a line from.
    Carries the invoice so the caller can name it in the 409 — kept out of HTTPException so this
    module stays free of FastAPI."""
    def __init__(self, invoice: Invoice):
        self.invoice = invoice


def booking_draft_claim(booking: Booking) -> Invoice | None:
    """The draft invoice claiming this booking, if any."""
    if booking.invoice_id is not None and booking.invoice.status == "draft":
        return booking.invoice
    return None


def enrollment_draft_claim(db: Session, enrollment: Enrollment) -> Invoice | None:
    """The draft invoice claiming this enrollment, if any — either a claimed booking of its
    contact's (per_session/per_hour), or a draft line pointing at the enrollment directly
    (per_month, which has no booking/item row of its own to claim).

    Not gated on the enrollment's current rate_unit: it's edited in place, so "current" could
    already be the incoming edit. Existence of a claiming line is checked directly instead, which
    stays correct no matter what the unit is changing to or from.
    """
    booking = (
        db.query(Booking)
        .join(Invoice, Booking.invoice_id == Invoice.id)
        .filter(Booking.attendee_id == enrollment.contact_id, Invoice.status == "draft")
        .first()
    )
    if booking is not None:
        return booking.invoice
    line = (
        db.query(InvoiceLine)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .filter(InvoiceLine.enrollment_id == enrollment.id, Invoice.status == "draft")
        .first()
    )
    return line.invoice if line is not None else None


def release_booking_claim(db: Session, booking: Booking) -> None:
    invoice = booking.invoice
    line = next((l for l in invoice.lines if l.booking_id == booking.id), None)
    # A booking can be claimed with no line of its own: see move_claim, where a finalized invoice
    # keeps its line on the original row. Clear the claim and leave the sent document alone.
    if line is None:
        booking.invoice_id = None
        return
    release_line(db, invoice, line)


def release_draft_claims(db: Session, bookings: list[Booking]) -> None:
    """Drop draft lines for rows about to stop existing — the series paths hard-delete their future
    occurrences, and a line left behind bills a session that isn't happening.

    A finalized invoice keeps its line: it already told the client they owed for that session, and
    `InvoiceLine.booking_id` nulls out on delete, leaving the frozen amount intact.
    """
    for booking in bookings:
        if booking.invoice_id is not None and booking.invoice.status == "draft":
            release_booking_claim(db, booking)


def move_claim(old: Booking, new: Booking) -> None:
    """Hand a reschedule's claim, and its line, to the row that now holds the session.

    Left on the old row the line bills a tombstone, release_line frees the wrong booking, and the
    replacement sits claimed with no line pointing at it — unbillable for good.

    A finalized invoice keeps its line on the original: it records what the client was sent, so it
    isn't rewritten. The claim still moves, which is what stops the replacement being swept again.
    """
    invoice = old.invoice
    if invoice is None:
        return
    new.invoice_id = invoice.id
    old.invoice_id = None
    if invoice.status != "draft":
        return
    line = next((l for l in invoice.lines if l.booking_id == old.id), None)
    if line is not None:
        line.booking_id = new.id


def release_enrollment_claims(db: Session, enrollment: Enrollment) -> None:
    """Releases everything currently claiming this enrollment — every claimed booking of its
    contact's, plus the monthly line if one's present. Used when an admin overrides the guard
    above instead of going to the invoice first."""
    bookings = (
        db.query(Booking)
        .join(Invoice, Booking.invoice_id == Invoice.id)
        .filter(Booking.attendee_id == enrollment.contact_id, Invoice.status == "draft")
        .all()
    )
    for booking in bookings:
        release_booking_claim(db, booking)
    lines = (
        db.query(InvoiceLine)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .filter(InvoiceLine.enrollment_id == enrollment.id, Invoice.status == "draft")
        .all()
    )
    for line in lines:
        release_line(db, line.invoice, line)


def set_invoice_sources(
        db: Session, invoice: Invoice, settings, booking_ids: list[int], item_ids: list[int],
    ) -> None:
    """Full-replacement: booking_ids/item_ids is the complete desired membership, not a delta.
    Diffed against what's currently claimed — newly listed ids get claimed and a line built for
    them, dropped ids go through release_line, unchanged ones (and the monthly line, which isn't
    selectable at all) are left alone so an existing adjustment survives."""
    tz = ZoneInfo(settings.business_timezone)
    current_booking_ids = {l.booking_id for l in invoice.lines if l.booking_id is not None}
    current_item_ids = {l.invoice_item_id for l in invoice.lines if l.invoice_item_id is not None}

    for line in [l for l in invoice.lines if l.booking_id is not None and l.booking_id not in booking_ids]:
        release_line(db, invoice, line)
    for line in [l for l in invoice.lines if l.invoice_item_id is not None and l.invoice_item_id not in item_ids]:
        release_line(db, invoice, line)

    to_add_booking_ids = set(booking_ids) - current_booking_ids
    if to_add_booking_ids:
        bookings = db.query(Booking).filter(
            Booking.id.in_(to_add_booking_ids), Booking.payer_id == invoice.payer_id, Booking.invoice_id.is_(None),
        ).all()
        if len(bookings) != len(to_add_booking_ids):
            raise ValueError("One or more bookings aren't this payer's, or are already claimed elsewhere")
        for booking in bookings:
            line = _booking_line(booking, tz)
            if line is not None:
                invoice.lines.append(line)
                booking.invoice = invoice

    to_add_item_ids = set(item_ids) - current_item_ids
    if to_add_item_ids:
        items = db.query(InvoiceItem).filter(
            InvoiceItem.id.in_(to_add_item_ids), InvoiceItem.payer_id == invoice.payer_id, InvoiceItem.invoice_id.is_(None),
        ).all()
        if len(items) != len(to_add_item_ids):
            raise ValueError("One or more items aren't this payer's, or are already claimed elsewhere")
        for item in items:
            invoice.lines.append(_item_line(item))
            item.invoice = invoice

    db.flush()
    invoice.total = round(sum(line_charged_amount(l) for l in invoice.lines), 2)


def generate_invoices(db: Session, period_start: date, period_end: date, settings,
                       payer_ids: list[int] | None = None) -> list[Invoice]:
    """Draft one invoice per payer for the period. One commit each, so a bad row costs that payer
    their invoice rather than rolling back everyone's. payer_ids narrows to a specific set;
    omitted, every payer with outstanding activity in the period."""
    targets = payer_ids if payer_ids is not None else payers_with_activity(db, period_start, period_end, settings)
    invoices = []
    for payer_id in targets:
        invoice = draft_invoice_for_payer(db, payer_id, settings, period_start, period_end)
        if invoice is not None:
            invoices.append(invoice)
    return invoices
