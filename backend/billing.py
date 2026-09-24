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


def line_amount(booking: Booking, enrollment: Enrollment | None) -> float | None:
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


def generate_invoices(db: Session, period_start: date, period_end: date, settings) -> list[Invoice]:
    """Draft one invoice per payer for the period. Re-runnable: a draft is rebuilt from scratch, a
    sent or paid one is left alone — once it's gone out, it's a record, not a calculation."""
    tz = ZoneInfo(settings.business_timezone)
    start_utc, end_utc = _period_bounds(period_start, period_end, settings)
    lines_by_payer: dict[int, list[InvoiceLine]] = defaultdict(list)

    # get all bookings that STARTED inside the billing period (if lesson started in period 1 and ended in period 2, it's under period 1)
    bookings = (
        db.query(Booking)
        .filter(Booking.status == "confirmed", Booking.start >= start_utc, Booking.start < end_utc)
        .all()
    )

    # for every booking, get the attendee and calculate the line amount: 
    # (either default link pricing, enrollment rate if exists, nothing if under a subscription or admin override charge).
    # If an amount exists, add it to the lines_by_payer dictionary under who the booking bills to. 
    # [payer 1: list of charges, payer 2: list of charges, etc.]
    for booking in bookings:
        attendee = booking.attendee
        amount = line_amount(booking, attendee.enrollment)
        if amount is None:
            continue
        start = booking.start if booking.start.tzinfo else booking.start.replace(tzinfo=UTC)
        lines_by_payer[booking.payer_id].append(InvoiceLine(
            booking_id=booking.id,
            description=f"{attendee.first_name} {attendee.last_name} — session {start.astimezone(tz):%b %-d}",
            amount=amount,
        ))

    ## Same process as above but for subscription lines. First, gather all active subscriptions from enrollments (ignore booking status like cancelled, no show, etc.)
    # Append subscription lines to the lines_by_payer dictionary on the enrolled person or their assigned payer if one exists.
    subscriptions = (
        db.query(Enrollment)
        .filter(
            Enrollment.rate_unit == "per_month",
            Enrollment.rate.isnot(None),
            Enrollment.is_active.is_(True),
            Enrollment.start_date < period_end, # new subscription in June isn't added to billing May 1-31st
        )
        .all()
    )
    for enrollment in subscriptions:
        # client is attendee, either bill to them or a payer if they have one under their enrollment.
        client = enrollment.contact
        lines_by_payer[enrollment.payer_id or enrollment.id].append(InvoiceLine(
            enrollment_id=enrollment.id,
            description=f"{client.first_name} {client.last_name} — monthly plan",
            amount=enrollment.rate,
        ))

    # Turn each payer's pile of lines into one invoice. Payer + period is unique, so finding an
    # existing row means this job is being re-run: reuse it if it's still a draft (keeping its id and
    # public_id) and leave it alone entirely once it's been sent. At that point it's a record of
    # what the client was told they owe, not a number we're still free to recalculate.
    invoices = []
    for payer_id, lines in lines_by_payer.items():
        existing = (
            db.query(Invoice)
            .filter(Invoice.payer_id == payer_id, Invoice.period_start == period_start)
            .first()
        )
        if existing is not None and existing.status != "draft":
            continue
        invoice = existing or Invoice(payer_id=payer_id, period_start=period_start, period_end=period_end)
        invoice.lines = lines          # delete-orphan drops the last run's lines instead of appending
        invoice.total = round(sum(line.amount for line in lines), 2)
        db.add(invoice)
        invoices.append(invoice)

    db.commit()
    for invoice in invoices:
        db.refresh(invoice)   # pick up public_id / created / status defaults
    return invoices
