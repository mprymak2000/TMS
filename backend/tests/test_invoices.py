"""Billing rules and invoice generation.

Bookings are built as rows rather than through POST /bookings/, which needs Google Calendar mocked.
Billing is a pure function over rows and doesn't care how they got there.
"""
from datetime import date, datetime, UTC
from uuid import uuid4

import pytest

from conftest import TestingSessionLocal
from models import Booking, BookingLink, BookingSeries, Settings

MARCH = (date(2026, 3, 1), date(2026, 4, 1))


def _contact(client, first, last="Test"):
    return client.post("/contacts/", json={
        "first_name": first, "last_name": last, "email": f"{first.lower()}@example.com",
    }).json()


def _enroll(client, contact_id, **terms):
    body = {"started_on": "2026-01-01", **terms}
    return client.put(f"/contacts/{contact_id}/enrollment", json=body).json()


@pytest.fixture
def link(client):
    """A link priced per session, so anyone without a rate of their own has something to fall to.

    Built as a row: POST /booking_links/ demands an availability entry, which needs a tutor and a
    schedule, none of which billing reads."""
    with TestingSessionLocal() as db:
        row = BookingLink(
            slug=f"session-{uuid4().hex[:8]}", duration_minutes=60,
            price=80, price_unit="per_session",
        )
        db.add(row)
        db.commit()
        return {"id": row.id}


@pytest.fixture
def tutor(client):
    return client.post("/tutors/", json={"first_name": "Tutor", "last_name": "T", "pay_rate": 0}).json()


def _booking(link, tutor, payer_id, attendee_id, day=10, hours=1, series_id=None, **kw):
    """One confirmed 1-hour booking in March 2026 unless told otherwise."""
    with TestingSessionLocal() as db:
        b = Booking(
            public_id=str(uuid4()),
            tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=payer_id, attendee_id=attendee_id,
            start=datetime(2026, 3, day, 16, 0, tzinfo=UTC),
            end=datetime(2026, 3, day, 16 + hours, 0, tzinfo=UTC),
            google_event_id=f"evt-{uuid4().hex[:8]}",
            series_id=series_id,
            **kw,
        )
        db.add(b)
        db.commit()
        return b.id


def _series(link, tutor, payer_id, attendee_id, covered):
    with TestingSessionLocal() as db:
        s = BookingSeries(
            public_id=str(uuid4()),
            tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=payer_id, attendee_id=attendee_id,
            dtstart=datetime(2026, 3, 3, 16, 0), dtend=datetime(2026, 3, 3, 17, 0),
            covered_by_subscription=covered,
        )
        db.add(s)
        db.commit()
        return s.id


def _generate(client, start=MARCH[0], end=MARCH[1]):
    r = client.post("/invoices/generate", json={
        "period_start": start.isoformat(), "period_end": end.isoformat(),
    })
    assert r.status_code == 201, r.text
    return r.json()


def _amounts(invoice):
    return sorted(line["amount"] for line in invoice["lines"])


# --- terms validation ---

def test_rate_without_unit_rejected(client):
    c = _contact(client, "NoUnit")
    r = client.put(f"/contacts/{c['id']}/enrollment", json={"started_on": "2026-01-01", "rate": 50})
    assert r.status_code == 422


def test_unknown_rate_unit_rejected(client):
    c = _contact(client, "BadUnit")
    r = client.put(f"/contacts/{c['id']}/enrollment",
                   json={"started_on": "2026-01-01", "rate": 50, "rate_unit": "per_fortnight"})
    assert r.status_code == 422


def test_unit_without_rate_is_allowed(client):
    """Plan picked, admin hasn't priced it yet — the state three exclusive columns couldn't express."""
    c = _contact(client, "Pending")
    r = client.put(f"/contacts/{c['id']}/enrollment",
                   json={"started_on": "2026-01-01", "rate_unit": "per_month"})
    assert r.status_code == 201
    assert r.json()["rate"] is None


def test_link_price_without_unit_rejected(client):
    r = client.post("/booking_links/", json={
        "slug": "no-unit", "duration_minutes": 60, "recurring": False, "price": 80,
        "cancel_mode": "auto", "reschedule_mode": "auto",
        "series_cancel_mode": "auto", "series_reschedule_mode": "auto", "availability": [],
    })
    assert r.status_code == 422


# --- the rate_unit matrix ---

def test_no_enrollment_bills_the_link_price(client, link, tutor):
    c = _contact(client, "Guest")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    assert _amounts(inv) == [80]


def test_per_session_bills_their_rate(client, link, tutor):
    c = _contact(client, "PerSession")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    _booking(link, tutor, c["id"], c["id"], hours=2)   # length is irrelevant
    inv = _generate(client)[0]
    assert _amounts(inv) == [55]


def test_per_hour_multiplies_by_length(client, link, tutor):
    c = _contact(client, "PerHour")
    _enroll(client, c["id"], rate=40, rate_unit="per_hour")
    _booking(link, tutor, c["id"], c["id"], hours=2)
    inv = _generate(client)[0]
    assert _amounts(inv) == [80]


def test_per_session_series_occurrence_still_bills(client, link, tutor):
    """Only a subscription covers anything. A negotiated per-session client pays for every session."""
    c = _contact(client, "Weekly")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    sid = _series(link, tutor, c["id"], c["id"], covered=True)   # flag is inert without per_month
    _booking(link, tutor, c["id"], c["id"], series_id=sid)
    inv = _generate(client)[0]
    assert _amounts(inv) == [55]


# --- subscriptions ---

def test_monthly_covered_series_produces_only_the_plan_line(client, link, tutor):
    c = _contact(client, "Sub")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    sid = _series(link, tutor, c["id"], c["id"], covered=True)
    for day in (3, 10, 17, 24):
        _booking(link, tutor, c["id"], c["id"], day=day, series_id=sid)
    inv = _generate(client)[0]
    assert _amounts(inv) == [400]
    assert inv["total"] == 400


def test_monthly_second_series_bills_on_top(client, link, tutor):
    """An extra weekly block before a test isn't what the plan bought."""
    c = _contact(client, "SubPlusBlock")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    covered = _series(link, tutor, c["id"], c["id"], covered=True)
    extra = _series(link, tutor, c["id"], c["id"], covered=False)
    _booking(link, tutor, c["id"], c["id"], day=3, series_id=covered)
    _booking(link, tutor, c["id"], c["id"], day=5, series_id=extra)
    inv = _generate(client)[0]
    assert _amounts(inv) == [80, 400]


def test_monthly_standalone_extra_bills_link_price(client, link, tutor):
    c = _contact(client, "SubPlusOne")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    sid = _series(link, tutor, c["id"], c["id"], covered=True)
    _booking(link, tutor, c["id"], c["id"], day=3, series_id=sid)
    _booking(link, tutor, c["id"], c["id"], day=14)
    inv = _generate(client)[0]
    assert _amounts(inv) == [80, 400]


def test_plan_line_appears_without_any_bookings(client):
    """A monthly fee is owed whether or not anyone showed up."""
    c = _contact(client, "NoShowsAtAll")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    inv = _generate(client)[0]
    assert _amounts(inv) == [400]


def test_plan_not_billed_before_it_starts(client):
    c = _contact(client, "StartsLater")
    _enroll(client, c["id"], rate=400, rate_unit="per_month", started_on="2026-04-01")
    assert _generate(client) == []


def test_closed_stint_is_not_billed(client):
    """They left in February, so March owes nothing."""
    c = _contact(client, "Left")
    _enroll(client, c["id"], rate=400, rate_unit="per_month", ended_on="2026-02-15")
    assert _generate(client) == []


def test_stint_closing_mid_period_still_bills(client):
    """Overlap is what counts, not whether it's still open. Proration is backlogged, so it's the
    full amount — see the roadmap."""
    c = _contact(client, "LeftMidMonth")
    _enroll(client, c["id"], rate=400, rate_unit="per_month", ended_on="2026-03-20")
    inv = _generate(client)[0]
    assert _amounts(inv) == [400]


# --- booking.charge override ---

def test_charge_beats_the_enrollment_rate(client, link, tutor):
    c = _contact(client, "Override")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    _booking(link, tutor, c["id"], c["id"], charge=20)
    inv = _generate(client)[0]
    assert _amounts(inv) == [20]


def test_zero_charge_is_a_freebie_not_a_missing_line(client, link, tutor):
    c = _contact(client, "Freebie")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    _booking(link, tutor, c["id"], c["id"], charge=0)
    inv = _generate(client)[0]
    assert _amounts(inv) == [0]
    assert inv["total"] == 0


def test_charge_bills_a_covered_session(client, link, tutor):
    c = _contact(client, "CoveredButCharged")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    sid = _series(link, tutor, c["id"], c["id"], covered=True)
    _booking(link, tutor, c["id"], c["id"], day=3, series_id=sid, charge=25)
    inv = _generate(client)[0]
    assert _amounts(inv) == [25, 400]


# --- which bookings count ---

def test_cancelled_does_not_bill(client, link, tutor):
    c = _contact(client, "Cancelled")
    _booking(link, tutor, c["id"], c["id"], status="cancelled")
    assert _generate(client) == []


def test_rescheduled_does_not_bill(client, link, tutor):
    """Its replacement row bills instead, so counting both would charge twice."""
    c = _contact(client, "Moved")
    _booking(link, tutor, c["id"], c["id"], status="rescheduled")
    assert _generate(client) == []


def test_no_show_bills(client, link, tutor):
    c = _contact(client, "NoShow")
    _booking(link, tutor, c["id"], c["id"], is_no_show=True)
    inv = _generate(client)[0]
    assert _amounts(inv) == [80]


def test_outside_the_period_does_not_bill(client, link, tutor):
    c = _contact(client, "February")
    with TestingSessionLocal() as db:
        db.add(Booking(
            public_id=str(uuid4()), tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=c["id"], attendee_id=c["id"],
            start=datetime(2026, 2, 10, 16, 0, tzinfo=UTC),
            end=datetime(2026, 2, 10, 17, 0, tzinfo=UTC),
            google_event_id="evt-feb",
        ))
        db.commit()
    assert _generate(client) == []


# --- who gets billed ---

def test_siblings_roll_up_to_one_invoice(client):
    """Two enrollments, two lines, one bill — Stripe's subscription items, not quantity 2."""
    rita = _contact(client, "Rita")
    marcus = _contact(client, "Marcus")
    ana = _contact(client, "Ana")
    _enroll(client, marcus["id"], rate=400, rate_unit="per_month", payer_id=rita["id"])
    _enroll(client, ana["id"], rate=350, rate_unit="per_month", payer_id=rita["id"])

    invoices = _generate(client)
    assert len(invoices) == 1
    assert invoices[0]["payer_id"] == rita["id"]
    assert _amounts(invoices[0]) == [350, 400]
    assert invoices[0]["total"] == 750


def test_no_payer_means_they_are_billed_themselves(client):
    c = _contact(client, "Adult")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    inv = _generate(client)[0]
    assert inv["payer_id"] == c["id"]


# --- re-running ---

# Create only, so a re-run is a no-op rather than a rebuild. That's what stops a bulk job silently
# undoing hand-edits on someone else's draft.
def test_regenerating_skips_what_already_exists(client, link, tutor):
    c = _contact(client, "Rerun")
    _booking(link, tutor, c["id"], c["id"])
    first = _generate(client)[0]

    assert _generate(client) == []
    after = client.get(f"/invoices/{first['id']}").json()
    assert _amounts(after) == [80]


def test_new_work_needs_a_refresh_not_a_regenerate(client, link, tutor):
    c = _contact(client, "MoreWork")
    _booking(link, tutor, c["id"], c["id"], day=5)
    inv = _generate(client)[0]
    _booking(link, tutor, c["id"], c["id"], day=12)

    assert _generate(client) == []                        # the invoice exists, so generate leaves it
    refreshed = client.post(f"/invoices/{inv['id']}/refresh").json()
    assert _amounts(refreshed) == [80, 80]


def test_a_sent_invoice_is_left_alone(client, link, tutor):
    c = _contact(client, "AlreadySent")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "sent"})
    _booking(link, tutor, c["id"], c["id"], day=20)   # more billable work appears

    assert _generate(client) == []
    after = client.get(f"/invoices/{inv['id']}").json()
    assert after["total"] == 80


# --- status ---

def test_status_flow(client, link, tutor):
    c = _contact(client, "Flow")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]

    sent = client.put(f"/invoices/{inv['id']}/status", json={"status": "sent"}).json()
    assert sent["sent_at"] is not None
    paid = client.put(f"/invoices/{inv['id']}/status", json={"status": "paid"}).json()
    assert paid["paid_at"] is not None


def test_cannot_go_backwards(client, link, tutor):
    c = _contact(client, "Backwards")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "sent"})
    assert client.put(f"/invoices/{inv['id']}/status", json={"status": "draft"}).status_code == 409


def test_only_a_draft_can_be_deleted(client, link, tutor):
    c = _contact(client, "Deletable")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "sent"})
    assert client.delete(f"/invoices/{inv['id']}").status_code == 409


# --- editing a draft ---

def test_add_line_retotals(client, link, tutor):
    c = _contact(client, "Materials")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    r = client.post(f"/invoices/{inv['id']}/lines", json={"description": "Workbook", "amount": 30})
    assert r.status_code == 201
    assert r.json()["total"] == 110


def test_edit_line_retotals(client, link, tutor):
    """The half-month case: fix the number rather than teaching the generator to prorate."""
    c = _contact(client, "HalfMonth")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    inv = _generate(client)[0]
    line_id = inv["lines"][0]["id"]
    r = client.put(f"/invoices/{inv['id']}/lines/{line_id}",
                   json={"description": "Monthly plan (half)", "amount": 200})
    assert r.json()["total"] == 200


def test_delete_line_retotals(client, link, tutor):
    c = _contact(client, "Removed")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    line_id = inv["lines"][0]["id"]
    r = client.delete(f"/invoices/{inv['id']}/lines/{line_id}")
    assert r.json()["total"] == 0
    assert r.json()["lines"] == []


def test_a_sent_invoice_cannot_be_edited(client, link, tutor):
    c = _contact(client, "Frozen")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "sent"})
    r = client.post(f"/invoices/{inv['id']}/lines", json={"description": "Late fee", "amount": 10})
    assert r.status_code == 409


# --- the snapshot ---

def test_a_line_survives_its_booking_being_deleted(client, link, tutor):
    """The invoice says what was billed, so it can't depend on the booking still existing."""
    c = _contact(client, "Vanishing")
    booking_id = _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    with TestingSessionLocal() as db:
        db.delete(db.query(Booking).filter(Booking.id == booking_id).first())
        db.commit()

    after = client.get(f"/invoices/{inv['id']}").json()
    assert after["total"] == 80
    assert after["lines"][0]["booking_id"] is None
    assert "Vanishing" in after["lines"][0]["description"]
