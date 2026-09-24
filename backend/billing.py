"""Turning a period's bookings into invoices.

Money is computed here and snapshotted onto the invoice, never stored on the booking as it accrues.
That's the Stripe shape: the booking is an operational record, the invoice is the document asserting
what was billed.

The rule, per booking:

    skip if status != 'confirmed'            cancelled/rescheduled don't bill; a no-show does
    skip if monthly client AND the series    what their plan pays for; a second series bills
           is covered_by_subscription
    amount = booking.charge                  admin override, including 0 for a freebie
          ?? enrollment.rate by rate_unit    per_session -> rate;  per_hour -> rate x hrs
          ?? link.price by price_unit        a monthly client's extra, or no enrollment at all

Plus one flat line per active per_month enrollment, regardless of attendance.
"""
from collections import defaultdict
from datetime import date, datetime, time, UTC
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import Booking, Enrollment, Invoice, InvoiceLine


def _hours(booking: Booking) -> float:
    # SQLite hands back naive datetimes; everything here is stored UTC. Same guard as booking_utils.
    start = booking.start if booking.start.tzinfo else booking.start.replace(tzinfo=UTC)
    end = booking.end if booking.end.tzinfo else booking.end.replace(tzinfo=UTC)
    return (end - start).total_seconds() / 3600


def _link_amount(booking: Booking) -> float | None:
    """What the link lists. Default price for booking if no overrides. Can be per hour or per session."""
    link = booking.booking_link
    if link is None or link.price is None:
        return None
    return link.price * _hours(booking) if link.price_unit == "per_hour" else link.price


def _line_amount(booking: Booking, enrollment: Enrollment | None) -> float | None:
    """Determine what this one booking bills. None means no line, so either covered by a subscription or unpriced."""
    
    # Admin override charge on booking. Always wins over enrollment or default link pricing.
    if booking.charge is not None:
        return booking.charge

    # Override the default link pricing with an enrolled client's special rates. Can be per hour or per session.
    if enrollment is not None and enrollment.rate is not None:
        if enrollment.rate_unit == "per_hour":
            return enrollment.rate * _hours(booking)
        if enrollment.rate_unit == "per_session":
            return enrollment.rate
        # Monthly plan, and this booking belongs to the series that plan pays for, so no line. A
        # second series (extra sessions on top of their usual schedule) isn't marked, so it bills.
        if enrollment.rate_unit == "per_month" and booking.series is not None \
                and booking.series.covered_by_subscription:
            return None

    # If no admin override and no enrollment rate applies, fall back to the default link pricing (mostly new contacts)
    return _link_amount(booking)


def _period_bounds(period_start: date, period_end: date, settings) -> tuple[datetime, datetime]:
    """Half-open [start, end) in business time, as UTC — bookings are stored UTC."""
    tz = ZoneInfo(settings.business_timezone)
    return (
        datetime.combine(period_start, time.min, tzinfo=tz).astimezone(UTC),
        datetime.combine(period_end, time.min, tzinfo=tz).astimezone(UTC),
    )


def _subscriptions(db: Session, period_start: date, period_end: date):
    """Monthly stints overlapping the period. Started in June isn't billed for May, and someone who
    left in April isn't billed for May either."""
    return db.query(Enrollment).filter(
        Enrollment.rate_unit == "per_month",
        Enrollment.rate.isnot(None),
        Enrollment.started_on < period_end,
        or_(Enrollment.ended_on.is_(None), Enrollment.ended_on > period_start),
    )


def payers_with_activity(db: Session, period_start: date, period_end: date, settings) -> set[int]:
    """Anyone the period could owe something for: bookings they paid for, or a monthly plan."""
    start_utc, end_utc = _period_bounds(period_start, period_end, settings)
    payers = {
        payer_id for (payer_id,) in db.query(Booking.payer_id).filter(
            Booking.status == "confirmed", Booking.start >= start_utc, Booking.start < end_utc,
        ).distinct()
    }
    for enrollment in _subscriptions(db, period_start, period_end):
        # client is attendee, either bill to them or a payer if they have one under their enrollment.
        payers.add(enrollment.payer_id or enrollment.contact_id)
    return payers


def _lines_for_payer(db: Session, payer_id: int, period_start: date, period_end: date, settings) -> list[InvoiceLine]:
    """Everything one payer owes for the period. The unit of work: generating and refreshing both
    build an invoice out of exactly this."""
    tz = ZoneInfo(settings.business_timezone)
    start_utc, end_utc = _period_bounds(period_start, period_end, settings)
    lines: list[InvoiceLine] = []

    # bookings that STARTED inside the billing period (if a session started in period 1 and ended in
    # period 2, it's under period 1)
    bookings = (
        db.query(Booking)
        .filter(
            Booking.payer_id == payer_id,
            Booking.status == "confirmed",
            Booking.start >= start_utc,
            Booking.start < end_utc,
        )
        .all()
    )
    # either default link pricing, enrollment rate if one exists, or nothing at all if the session is
    # covered by a subscription. An admin charge on the booking beats all of it.
    for booking in bookings:
        attendee = booking.attendee
        amount = _line_amount(booking, attendee.current_enrollment)
        if amount is None:
            continue
        start = booking.start if booking.start.tzinfo else booking.start.replace(tzinfo=UTC)
        lines.append(InvoiceLine(
            booking_id=booking.id,
            description=f"{attendee.first_name} {attendee.last_name} — session {start.astimezone(tz):%b %-d}",
            amount=amount,
        ))

    # Subscription lines aren't derived from bookings at all — a monthly fee is owed whether or not
    # anyone showed up.
    for enrollment in _subscriptions(db, period_start, period_end):
        if (enrollment.payer_id or enrollment.contact_id) != payer_id:
            continue
        client = enrollment.contact
        lines.append(InvoiceLine(
            enrollment_id=enrollment.id,
            description=f"{client.first_name} {client.last_name} — monthly plan",
            amount=enrollment.rate,
        ))

    return lines


def _is_generated(line: InvoiceLine) -> bool:
    """Produced by the rules and untouched since, so a refresh is free to replace it. A hand-added
    line has no source, and an adjusted one kept what the rules said in `computed`."""
    return line.computed is None and (line.booking_id is not None or line.enrollment_id is not None)


def generate_invoice_for_payer(db: Session, payer_id: int, period_start: date, period_end: date,
                               settings) -> Invoice | None:
    """Create only. An existing invoice for this payer and period means this already ran, and a bulk
    job has no business rewriting it — rebuilding one is refresh_invoice, which is something you ask
    for rather than something that happens to you."""
    exists = (
        db.query(Invoice)
        .filter(Invoice.payer_id == payer_id, Invoice.period_start == period_start)
        .first()
    )
    if exists is not None:
        return None
    lines = _lines_for_payer(db, payer_id, period_start, period_end, settings)
    if not lines:
        return None
    invoice = Invoice(payer_id=payer_id, period_start=period_start, period_end=period_end, lines=lines)
    invoice.total = round(sum(line.amount for line in lines), 2)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)   # pick up public_id / created / status defaults
    return invoice


def generate_invoices(db: Session, period_start: date, period_end: date, settings) -> list[Invoice]:
    """Draft one invoice per payer for the period. One commit each, so a bad row costs that payer
    their invoice rather than rolling back everyone's."""
    invoices = []
    for payer_id in payers_with_activity(db, period_start, period_end, settings):
        invoice = generate_invoice_for_payer(db, payer_id, period_start, period_end, settings)
        if invoice is not None:
            invoices.append(invoice)
    return invoices


def refresh_invoice(db: Session, invoice: Invoice, settings) -> Invoice:
    """Rebuild a draft's generated lines against current data, keeping anything a human touched."""
    kept = [line for line in invoice.lines if not _is_generated(line)]
    invoice.lines = kept + _lines_for_payer(db, invoice.payer_id, invoice.period_start, invoice.period_end, settings)
    db.flush()
    invoice.total = round(sum(line.amount for line in invoice.lines), 2)
    db.commit()
    db.refresh(invoice)
    return invoice
