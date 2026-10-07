"""Billing rules, invoice lifecycle, and the price catalog.

Bookings are built as rows rather than through POST /bookings/, which needs Google Calendar mocked.
Billing is a pure function over rows and doesn't care how they got there — but it reads the price
and rate *pointers* frozen onto a booking, so the helpers below set those the way create_booking
does: price off the link, rate off the attendee's open enrollment.
"""
from datetime import date, datetime, time, timedelta, UTC
from decimal import Decimal
from uuid import uuid4

import pytest

from conftest import TestingSessionLocal
from models import (
    Booking, BookingLink, BookingLinkAvailability, BookingSeries, Enrollment, Invoice, Price,
    Schedule, ScheduleDay,
)

MARCH = (date(2026, 3, 1), date(2026, 4, 1))


def _contact(client, first, last="Test"):
    return client.post("/contacts/", json={
        "first_name": first, "last_name": last, "email": f"{first.lower()}@example.com",
    }).json()


def _enroll(client, contact_id, **terms):
    body = {"started_on": "2026-01-01", **terms}
    return client.put(f"/contacts/{contact_id}/enrollment", json=body).json()


@pytest.fixture
def tutor(client):
    return client.post("/tutors/", json={
        "first_name": "Tutor", "last_name": "T", "pay_rate": 0, "calendar_id": "cal@example.com",
    }).json()


@pytest.fixture
def link(client, tutor):
    """A link priced per session, so anyone without a rate of their own has something to fall to.

    Built as rows: POST /booking_links/ wants an availability entry, and billing reads none of that.
    The wide-open schedule is here only so tests that drive a real booking route (reschedule, which
    checks the slot sits in the tutor's schedule) have one to satisfy."""
    with TestingSessionLocal() as db:
        price = Price(amount=Decimal("80.00"), unit="per_session")
        db.add(price)
        db.flush()
        row = BookingLink(slug=f"session-{uuid4().hex[:8]}", duration_minutes=60, price_id=price.id)
        schedule = Schedule(tutor_id=tutor["id"], name=f"All-{uuid4().hex[:6]}",
                            is_default=True, timezone="UTC")
        db.add_all([row, schedule])
        db.flush()
        for day in range(7):
            db.add(ScheduleDay(schedule_id=schedule.id, day_of_week=day,
                               start_time=time(0, 0), end_time=time(23, 59)))
        db.add(BookingLinkAvailability(booking_link_id=row.id, tutor_id=tutor["id"],
                                       schedule_id=schedule.id))
        db.commit()
        return {"id": row.id, "price_id": price.id}


def _rate_id(db, contact_id):
    """The attendee's open-stint rate, which is what create_booking freezes onto a booking."""
    enrollment = (
        db.query(Enrollment)
        .filter(Enrollment.contact_id == contact_id, Enrollment.ended_on.is_(None))
        .first()
    )
    return enrollment.rate_id if enrollment else None


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
            price_id=kw.pop("price_id", link["price_id"]),
            rate_id=kw.pop("rate_id", _rate_id(db, attendee_id)),
            **kw,
        )
        db.add(b)
        db.commit()
        return {"id": b.id, "public_id": b.public_id}


def _series(link, tutor, payer_id, attendee_id, covered_by_enrollment_id=None):
    with TestingSessionLocal() as db:
        s = BookingSeries(
            public_id=str(uuid4()),
            tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=payer_id, attendee_id=attendee_id,
            dtstart=datetime(2026, 3, 3, 16, 0), dtend=datetime(2026, 3, 3, 17, 0),
            price_id=link["price_id"], rate_id=_rate_id(db, attendee_id),
            covered_by_enrollment_id=covered_by_enrollment_id,
            google_event_id=f"evt-series-{uuid4().hex[:8]}",
        )
        db.add(s)
        db.commit()
        return s.id


def _generate(client, start=MARCH[0], end=MARCH[1], **kw):
    r = client.post("/invoices/generate", json={
        "period_start": start.isoformat(), "period_end": end.isoformat(), **kw,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _amounts(invoice):
    return sorted(line["amount"] for line in invoice["lines"])


def _booking_payload(client, ref, **overrides):
    """PUT /bookings/{ref} replaces rather than patches, so a caller has to send the whole row."""
    b = client.get(f"/bookings/{ref}").json()
    body = {
        "booking_link_id": b["booking_link_id"],
        "booking_type_id": b["booking_type_id"],
        "cancel_mode": b["cancel_mode"],
        "cancel_notice_minutes": b["cancel_notice_minutes"],
        "reschedule_mode": b["reschedule_mode"],
        "reschedule_notice_minutes": b["reschedule_notice_minutes"],
        "is_no_show": b["is_no_show"],
        "charge": b["charge"],
    }
    return body | overrides


# --- terms validation ---

def test_rate_without_unit_rejected(client):
    c = _contact(client, "NoUnit")
    r = client.put(f"/contacts/{c['id']}/enrollment",
                   json={"started_on": "2026-01-01", "rate": 50})
    assert r.status_code == 422


def test_unknown_rate_unit_rejected(client):
    c = _contact(client, "BadUnit")
    r = client.put(f"/contacts/{c['id']}/enrollment",
                   json={"started_on": "2026-01-01", "rate": 50, "rate_unit": "per_fortnight"})
    assert r.status_code == 422


def test_unit_without_rate_is_allowed(client):
    """Plan picked, not yet priced. No Price row results, so nothing bills."""
    c = _contact(client, "UnpricedPlan")
    r = client.put(f"/contacts/{c['id']}/enrollment",
                   json={"started_on": "2026-01-01", "rate_unit": "per_month"})
    assert r.status_code == 201
    assert r.json()["rate"] is None
    assert r.json()["rate_id"] is None


def test_link_price_without_unit_rejected(client):
    r = client.post("/booking_links/", json={
        "slug": "no-unit", "duration_minutes": 60, "price": 80,
        "availability": [],
    })
    assert r.status_code == 422


# --- the rate_unit matrix ---

def test_no_enrollment_bills_the_link_price(client, link, tutor):
    c = _contact(client, "Walkin")
    _booking(link, tutor, c["id"], c["id"])
    assert _amounts(_generate(client)[0]) == [80]


def test_per_session_bills_their_rate(client, link, tutor):
    c = _contact(client, "Session")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    _booking(link, tutor, c["id"], c["id"])
    assert _amounts(_generate(client)[0]) == [55]


def test_per_hour_multiplies_by_length(client, link, tutor):
    c = _contact(client, "Hourly")
    _enroll(client, c["id"], rate=40, rate_unit="per_hour")
    _booking(link, tutor, c["id"], c["id"], hours=2)
    assert _amounts(_generate(client)[0]) == [80]


def test_per_session_series_occurrence_still_bills(client, link, tutor):
    """Only a subscription covers anything. A negotiated per-session client pays for every session."""
    c = _contact(client, "Weekly")
    enrollment = _enroll(client, c["id"], rate=55, rate_unit="per_session")
    sid = _series(link, tutor, c["id"], c["id"], covered_by_enrollment_id=enrollment["id"])
    _booking(link, tutor, c["id"], c["id"], series_id=sid)
    assert _amounts(_generate(client)[0]) == [55]


# --- subscriptions ---

def test_monthly_covered_series_produces_only_the_plan_line(client, link, tutor):
    c = _contact(client, "Monthly")
    enrollment = _enroll(client, c["id"], rate=400, rate_unit="per_month")
    sid = _series(link, tutor, c["id"], c["id"], covered_by_enrollment_id=enrollment["id"])
    _booking(link, tutor, c["id"], c["id"], series_id=sid)
    _booking(link, tutor, c["id"], c["id"], day=17, series_id=sid)
    assert _amounts(_generate(client)[0]) == [400]


def test_monthly_second_series_bills_on_top(client, link, tutor):
    """Extras on top of the usual schedule aren't covered, so they fall to the link's price."""
    c = _contact(client, "MonthlyExtra")
    enrollment = _enroll(client, c["id"], rate=400, rate_unit="per_month")
    covered = _series(link, tutor, c["id"], c["id"], covered_by_enrollment_id=enrollment["id"])
    extra = _series(link, tutor, c["id"], c["id"])
    _booking(link, tutor, c["id"], c["id"], series_id=covered)
    _booking(link, tutor, c["id"], c["id"], day=18, series_id=extra)
    assert _amounts(_generate(client)[0]) == [80, 400]


def test_monthly_standalone_extra_bills_link_price(client, link, tutor):
    c = _contact(client, "MonthlyOneOff")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    _booking(link, tutor, c["id"], c["id"])
    assert _amounts(_generate(client)[0]) == [80, 400]


def test_plan_line_appears_without_any_bookings(client):
    """A monthly fee is owed whether or not anyone showed up."""
    c = _contact(client, "NoShowMonth")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    assert _amounts(_generate(client)[0]) == [400]


def test_plan_not_billed_before_it_starts(client):
    c = _contact(client, "StartsLater")
    _enroll(client, c["id"], rate=400, rate_unit="per_month", started_on="2026-04-01")
    assert _generate(client) == []


def test_closed_stint_is_not_billed(client):
    c = _contact(client, "Left")
    _enroll(client, c["id"], rate=400, rate_unit="per_month", ended_on="2026-02-15")
    assert _generate(client) == []


def test_stint_closing_mid_period_still_bills(client):
    """Proration is a separate feature; the line is editable, which is the answer for now."""
    c = _contact(client, "LeftMidMonth")
    _enroll(client, c["id"], rate=400, rate_unit="per_month", ended_on="2026-03-20")
    assert _amounts(_generate(client)[0]) == [400]


# --- booking.charge override ---

def test_charge_beats_the_enrollment_rate(client, link, tutor):
    c = _contact(client, "Overridden")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    _booking(link, tutor, c["id"], c["id"], charge=20)
    assert _amounts(_generate(client)[0]) == [20]


def test_zero_charge_is_a_freebie_not_a_missing_line(client, link, tutor):
    c = _contact(client, "Freebie")
    _enroll(client, c["id"], rate=55, rate_unit="per_session")
    _booking(link, tutor, c["id"], c["id"], charge=0)
    assert _amounts(_generate(client)[0]) == [0]


def test_charge_bills_a_covered_session(client, link, tutor):
    """An override is the admin saying this one costs extra, plan or no plan."""
    c = _contact(client, "CoveredButCharged")
    enrollment = _enroll(client, c["id"], rate=400, rate_unit="per_month")
    sid = _series(link, tutor, c["id"], c["id"], covered_by_enrollment_id=enrollment["id"])
    _booking(link, tutor, c["id"], c["id"], series_id=sid, charge=25)
    assert _amounts(_generate(client)[0]) == [25, 400]


# --- which bookings count ---

def test_cancelled_does_not_bill(client, link, tutor):
    c = _contact(client, "Cancelled")
    _booking(link, tutor, c["id"], c["id"], status="cancelled")
    assert _generate(client) == []


def test_rescheduled_does_not_bill(client, link, tutor):
    """The replacement row bills; the vacated one would double it."""
    c = _contact(client, "Moved")
    _booking(link, tutor, c["id"], c["id"], status="rescheduled")
    assert _generate(client) == []


def test_no_show_bills(client, link, tutor):
    c = _contact(client, "NoShow")
    _booking(link, tutor, c["id"], c["id"], is_no_show=True)
    assert _amounts(_generate(client)[0]) == [80]


def test_cancelled_with_a_charge_still_bills_for_the_charge(client, link, tutor):
    c = _contact(client, "LateCancel")
    _booking(link, tutor, c["id"], c["id"], status="cancelled", charge=40)
    assert _amounts(_generate(client)[0]) == [40]


def test_outside_the_period_does_not_bill(client, link, tutor):
    """Half-open [start, end): a session starting in period 1 belongs to period 1."""
    c = _contact(client, "Boundary")
    with TestingSessionLocal() as db:
        for when in (datetime(2026, 2, 28, 16, tzinfo=UTC), datetime(2026, 4, 1, 16, tzinfo=UTC)):
            db.add(Booking(
                public_id=str(uuid4()), tutor_id=tutor["id"], booking_link_id=link["id"],
                payer_id=c["id"], attendee_id=c["id"], start=when,
                end=when.replace(hour=17), google_event_id=f"evt-{uuid4().hex[:8]}",
                price_id=link["price_id"],
            ))
        db.commit()
    assert _generate(client) == []


# --- who gets billed ---

def test_siblings_roll_up_to_one_invoice(client):
    rita = _contact(client, "Rita")
    marcus = _contact(client, "Marcus")
    ana = _contact(client, "Ana")
    _enroll(client, marcus["id"], rate=400, rate_unit="per_month", payer_id=rita["id"])
    _enroll(client, ana["id"], rate=350, rate_unit="per_month", payer_id=rita["id"])

    invoices = _generate(client)
    assert len(invoices) == 1
    assert invoices[0]["payer_id"] == rita["id"]
    assert _amounts(invoices[0]) == [350, 400]


def test_no_payer_means_they_are_billed_themselves(client):
    c = _contact(client, "SelfPay")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    assert _generate(client)[0]["payer_id"] == c["id"]


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


def test_an_overlapping_period_is_rejected(client, link, tutor):
    """The monthly fee carries no claim of its own, so overlapping periods are what would double
    it. Catching up on something missed is the ad-hoc path instead."""
    c = _contact(client, "Overlap")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    _generate(client)

    r = client.post("/invoices/", json={
        "payer_id": c["id"], "period_start": "2026-03-15", "period_end": "2026-04-15",
    })
    assert r.status_code == 409


def test_new_work_is_selected_onto_the_draft(client, link, tutor):
    """No refresh: a line is generated once and frozen. Work that appears afterwards is picked up
    by selecting it, which is also what the draft editor's checkboxes do."""
    c = _contact(client, "MoreWork")
    first = _booking(link, tutor, c["id"], c["id"], day=5)
    inv = _generate(client)[0]
    second = _booking(link, tutor, c["id"], c["id"], day=12)

    assert _generate(client) == []   # the invoice exists, so generate leaves it alone
    r = client.put(f"/invoices/{inv['id']}/lines", json={
        "booking_ids": [first["public_id"], second["public_id"]], "item_ids": [],
    })
    assert _amounts(r.json()) == [80, 80]


def test_deselecting_releases_the_booking(client, link, tutor):
    c = _contact(client, "Deselected")
    first = _booking(link, tutor, c["id"], c["id"], day=5)
    second = _booking(link, tutor, c["id"], c["id"], day=12)
    inv = _generate(client)[0]
    assert len(inv["lines"]) == 2

    r = client.put(f"/invoices/{inv['id']}/lines", json={
        "booking_ids": [first["public_id"]], "item_ids": [],
    })
    assert _amounts(r.json()) == [80]
    # Released, so a fresh ad-hoc invoice can pick the dropped one up again.
    again = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [second["public_id"]]})
    assert _amounts(again.json()) == [80]


def test_a_finalized_invoice_is_left_alone(client, link, tutor):
    c = _contact(client, "AlreadySent")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"})
    _booking(link, tutor, c["id"], c["id"], day=20)   # more billable work appears

    assert _generate(client) == []
    after = client.get(f"/invoices/{inv['id']}").json()
    assert after["total"] == 80


# --- claims ---

def test_a_claimed_booking_is_not_swept_again(client, link, tutor):
    c = _contact(client, "Claimed")
    _booking(link, tutor, c["id"], c["id"])
    _generate(client)
    # Ad-hoc, so no period guard is involved — the claim alone is what stops a second line.
    r = client.post("/invoices/", json={"payer_id": c["id"]})
    assert r.status_code == 409


def test_deleting_a_line_releases_its_booking(client, link, tutor):
    c = _contact(client, "LineDeleted")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]

    r = client.delete(f"/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}")
    assert r.status_code == 200
    assert r.json()["lines"] == []
    assert r.json()["total"] == 0
    # Released back, so it's billable again.
    assert _amounts(client.post("/invoices/", json={"payer_id": c["id"]}).json()) == [80]


# --- a draft doesn't hold its sources still ---
# Editing a booking's charge or a client's rate while a draft exists is allowed. The line's amount
# was frozen when it was generated, so the edit can't corrupt the invoice — the draft just shows the
# older number until someone regenerates it. Guarding this was tried and removed: no platform
# checked does it (Cliniko only marks the appointment as invoiced), and it meant a 409 mid-workflow.

def test_charge_edits_freely_while_on_a_draft(client, link, tutor):
    c = _contact(client, "ChargeEdit")
    b = _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]

    r = client.put(f"/bookings/{b['public_id']}",
                   json=_booking_payload(client, b["public_id"], charge=25))
    assert r.status_code == 200
    assert r.json()["charge"] == 25
    # The draft is untouched — frozen at generation, not re-derived.
    assert _amounts(client.get(f"/invoices/{inv['id']}").json()) == [80]


def test_rate_edits_freely_while_a_draft_carries_the_plan_line(client):
    c = _contact(client, "RateEdit")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    inv = _generate(client)[0]

    r = client.put(f"/contacts/{c['id']}/enrollment",
                   json={"started_on": "2026-01-01", "rate": 450, "rate_unit": "per_month"})
    assert r.status_code == 200
    assert r.json()["rate"] == 450
    assert _amounts(client.get(f"/invoices/{inv['id']}").json()) == [400]


def test_regenerating_picks_up_the_new_rate(client):
    """The documented way to pull a drifted draft back in line: delete it and generate again."""
    c = _contact(client, "Regenerated")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    inv = _generate(client)[0]
    client.put(f"/contacts/{c['id']}/enrollment",
               json={"started_on": "2026-01-01", "rate": 450, "rate_unit": "per_month"})

    client.delete(f"/invoices/{inv['id']}")
    assert _amounts(_generate(client)[0]) == [450]


# --- status ---

def test_status_flow(client, link, tutor):
    c = _contact(client, "Flow")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    assert inv["status"] == "draft"
    assert inv["number"] is None

    sent = client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"}).json()
    assert sent["status"] == "finalized"
    assert sent["sent_at"] is not None
    assert sent["number"].startswith("INV-")

    void = client.put(f"/invoices/{inv['id']}/status", json={"status": "void"}).json()
    assert void["status"] == "void"


def test_invoice_numbers_are_sequential_within_a_year(client, link, tutor):
    """Allocated at finalize, not at creation, so deleting drafts can't gap the sequence."""
    a, b = _contact(client, "NumA"), _contact(client, "NumB")
    _booking(link, tutor, a["id"], a["id"])
    _booking(link, tutor, b["id"], b["id"])
    invoices = _generate(client)

    numbers = [
        client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"}).json()["number"]
        for inv in invoices
    ]
    seqs = sorted(int(n.rsplit("-", 1)[1]) for n in numbers)
    assert seqs == [1, 2]


def test_payment_status_moves_independently_of_status(client, link, tutor):
    c = _contact(client, "Paid")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"})

    paid = client.put(f"/invoices/{inv['id']}/payment", json={"payment_status": "paid"}).json()
    assert paid["payment_status"] == "paid"
    assert paid["paid_at"] is not None
    assert paid["status"] == "finalized"   # unchanged


def test_a_draft_has_nothing_to_be_paid(client, link, tutor):
    c = _contact(client, "UnpaidDraft")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    r = client.put(f"/invoices/{inv['id']}/payment", json={"payment_status": "paid"})
    assert r.status_code == 409


def test_cannot_go_backwards(client, link, tutor):
    c = _contact(client, "NoReverse")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"})
    r = client.put(f"/invoices/{inv['id']}/status", json={"status": "draft"})
    assert r.status_code == 409


def test_only_a_draft_can_be_deleted(client, link, tutor):
    c = _contact(client, "Undeletable")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"})
    assert client.delete(f"/invoices/{inv['id']}").status_code == 409


# --- adjusting a line ---

def test_a_pending_item_is_added_via_its_own_endpoint(client, link, tutor):
    """A charge with no booking behind it is an InvoiceItem, added before generation, not a line
    typed straight onto the draft."""
    c = _contact(client, "Materials")
    _booking(link, tutor, c["id"], c["id"])
    client.post("/invoice-items/", json={"payer_id": c["id"], "description": "Workbook", "amount": 30})
    inv = _generate(client)[0]
    assert inv["total"] == 110


def test_a_flat_adjustment_retotals_without_overwriting_amount(client, link, tutor):
    """The half-month case: correct the number rather than teach the generator to prorate. `amount`
    stays as computed so the invoice can show was-and-is."""
    c = _contact(client, "HalfMonth")
    _enroll(client, c["id"], rate=400, rate_unit="per_month")
    inv = _generate(client)[0]
    r = client.put(f"/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}",
                   json={"adjustment_amount": -200})
    line = r.json()["lines"][0]
    assert line["amount"] == 400            # untouched
    assert line["adjustment_amount"] == -200
    assert line["charged_amount"] == 200
    assert r.json()["total"] == 200


def test_a_percent_adjustment_retotals(client, link, tutor):
    c = _contact(client, "Discounted")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    r = client.put(f"/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}",
                   json={"adjustment_percent": 25})
    assert r.json()["lines"][0]["charged_amount"] == 60
    assert r.json()["total"] == 60


def test_the_two_adjustment_kinds_are_mutually_exclusive(client, link, tutor):
    c = _contact(client, "BothKinds")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    r = client.put(f"/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}",
                   json={"adjustment_amount": -10, "adjustment_percent": 10})
    assert r.status_code == 422


def test_an_adjustment_is_undoable(client, link, tutor):
    c = _contact(client, "Undone")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    line_id = inv["lines"][0]["id"]
    client.put(f"/invoices/{inv['id']}/lines/{line_id}", json={"adjustment_amount": -30})
    r = client.put(f"/invoices/{inv['id']}/lines/{line_id}", json={})
    assert r.json()["lines"][0]["adjustment_amount"] is None
    assert r.json()["total"] == 80


def test_adjusting_one_line_leaves_its_neighbour_alone(client, link, tutor):
    """Adjustments live on the line, so two dateless items can't collide the way a shared
    (booking_id, enrollment_id) key once let them."""
    c = _contact(client, "TwoItems")
    _booking(link, tutor, c["id"], c["id"])
    a = client.post("/invoice-items/", json={"payer_id": c["id"], "description": "Late fee", "amount": 10}).json()
    b = client.post("/invoice-items/", json={"payer_id": c["id"], "description": "Materials", "amount": 20}).json()
    inv = _generate(client)[0]

    line_a = next(l for l in inv["lines"] if l["invoice_item_id"] == a["id"])
    r = client.put(f"/invoices/{inv['id']}/lines/{line_a['id']}", json={"adjustment_amount": -5})
    lines = {l["invoice_item_id"]: l for l in r.json()["lines"]}
    assert lines[a["id"]]["charged_amount"] == 5
    assert lines[b["id"]]["charged_amount"] == 20


def test_a_finalized_invoice_cannot_be_edited(client, link, tutor):
    c = _contact(client, "Frozen")
    _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    client.put(f"/invoices/{inv['id']}/status", json={"status": "finalized"})
    r = client.put(f"/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}",
                   json={"adjustment_amount": -10})
    assert r.status_code == 409


# --- the snapshot ---

def test_a_line_survives_its_booking_being_deleted(client, link, tutor):
    """The invoice says what was billed, so it can't depend on the booking still existing."""
    c = _contact(client, "Vanishing")
    b = _booking(link, tutor, c["id"], c["id"])
    inv = _generate(client)[0]
    with TestingSessionLocal() as db:
        db.delete(db.query(Booking).filter(Booking.id == b["id"]).first())
        db.commit()

    after = client.get(f"/invoices/{inv['id']}").json()
    assert after["total"] == 80
    assert after["lines"][0]["booking_id"] is None
    assert "Vanishing" in after["lines"][0]["description"]


# --- prices ---

def test_the_same_terms_share_one_price_row(client):
    """Dedup is what makes a bulk change one UPDATE rather than a loop over clients."""
    a, b = _contact(client, "ShareA"), _contact(client, "ShareB")
    first = _enroll(client, a["id"], rate=100, rate_unit="per_month")
    second = _enroll(client, b["id"], rate=100, rate_unit="per_month")
    assert first["rate_id"] == second["rate_id"]


def test_a_different_unit_is_a_different_price(client):
    a, b = _contact(client, "UnitA"), _contact(client, "UnitB")
    first = _enroll(client, a["id"], rate=100, rate_unit="per_month")
    second = _enroll(client, b["id"], rate=100, rate_unit="per_session")
    assert first["rate_id"] != second["rate_id"]


def test_usage_counts_who_is_on_a_price(client, link, tutor):
    a, b = _contact(client, "UsageA"), _contact(client, "UsageB")
    enrollment = _enroll(client, a["id"], rate=100, rate_unit="per_month")
    _enroll(client, b["id"], rate=100, rate_unit="per_month")
    usage = client.get(f"/prices/{enrollment['rate_id']}/usage").json()
    assert usage["enrollments"] == 2


def test_supersede_moves_everyone_on_that_price(client):
    a, b = _contact(client, "RaiseA"), _contact(client, "RaiseB")
    enrollment = _enroll(client, a["id"], rate=100, rate_unit="per_month")
    _enroll(client, b["id"], rate=100, rate_unit="per_month")

    new = client.post(f"/prices/{enrollment['rate_id']}/supersede", json={"amount": 110}).json()
    assert new["amount"] == 110
    for contact in (a, b):
        current = client.get(f"/contacts/{contact['id']}").json()["enrollment"]
        assert current["rate"] == 110
        assert current["rate_id"] == new["id"]


def test_supersede_can_grandfather_someone(client):
    """Omitting them from the repointing UPDATE is the whole mechanism."""
    a, b = _contact(client, "MovedOn"), _contact(client, "Grandfathered")
    enrollment_a = _enroll(client, a["id"], rate=100, rate_unit="per_month")
    enrollment_b = _enroll(client, b["id"], rate=100, rate_unit="per_month")

    client.post(f"/prices/{enrollment_a['rate_id']}/supersede",
                json={"amount": 110, "exclude_enrollment_ids": [enrollment_b["id"]]})
    assert client.get(f"/contacts/{a['id']}").json()["enrollment"]["rate"] == 110
    assert client.get(f"/contacts/{b['id']}").json()["enrollment"]["rate"] == 100


def test_a_superseded_price_drops_out_of_the_catalog(client):
    c = _contact(client, "Superseded")
    enrollment = _enroll(client, c["id"], rate=100, rate_unit="per_month")
    old_id = enrollment["rate_id"]
    client.post(f"/prices/{old_id}/supersede", json={"amount": 110})

    live = [p["id"] for p in client.get("/prices/").json()]
    assert old_id not in live
    assert old_id in [p["id"] for p in client.get("/prices/?include_archived=true").json()]


def test_a_price_still_in_use_stays_live(client):
    c = _contact(client, "StillOn")
    enrollment = _enroll(client, c["id"], rate=100, rate_unit="per_month")
    old_id = enrollment["rate_id"]
    client.post(f"/prices/{old_id}/supersede",
                json={"amount": 110, "exclude_enrollment_ids": [enrollment["id"]]})
    assert old_id in [p["id"] for p in client.get("/prices/").json()]


def test_superseding_twice_is_rejected(client):
    c = _contact(client, "Twice")
    enrollment = _enroll(client, c["id"], rate=100, rate_unit="per_month")
    old_id = enrollment["rate_id"]
    client.post(f"/prices/{old_id}/supersede", json={"amount": 110})
    assert client.post(f"/prices/{old_id}/supersede", json={"amount": 120}).status_code == 409


def test_a_booking_keeps_the_price_it_froze(client, link, tutor):
    """The point of the whole indirection: raising a rate can't reprice a past session."""
    c = _contact(client, "Frozen2")
    enrollment = _enroll(client, c["id"], rate=40, rate_unit="per_hour")
    _booking(link, tutor, c["id"], c["id"], hours=2)
    client.post(f"/prices/{enrollment['rate_id']}/supersede", json={"amount": 60})

    assert client.get(f"/contacts/{c['id']}").json()["enrollment"]["rate"] == 60
    assert _amounts(_generate(client)[0]) == [80]   # 2h at the old 40, not the new 60


# --- money is exact ---

def test_totals_are_exact_decimals(client, link, tutor):
    """Numeric, not Float: three lines of 33.33 sum to exactly 99.99."""
    c = _contact(client, "Exact")
    _enroll(client, c["id"], rate=33.33, rate_unit="per_session")
    for day in (5, 12, 19):
        _booking(link, tutor, c["id"], c["id"], day=day)
    inv = _generate(client)[0]

    with TestingSessionLocal() as db:
        total = db.query(Invoice).filter(Invoice.public_id == inv["id"]).first().total
    assert total == Decimal("99.99")
    assert str(total) == "99.99"


# --- reschedule carries the claim ---

def test_rescheduling_a_claimed_booking_does_not_bill_twice(client, link, tutor):
    """The replacement row inherits invoice_id. Without that it arrives unclaimed and `confirmed`,
    so the next sweep bills the same session a second time while the draft keeps the original line."""
    from unittest.mock import MagicMock, patch

    c = _contact(client, "Rebooked")
    b = _booking(link, tutor, c["id"], c["id"], day=10)
    inv = _generate(client)[0]
    assert _amounts(inv) == [80]

    svc = MagicMock()
    svc.events().insert().execute.return_value = {"id": "evt-new"}
    svc.events().delete().execute.return_value = {}
    with patch("routers.bookings.get_calendar_service", return_value=svc):
        r = client.post(f"/bookings/{b['public_id']}/reschedule", json={
            "tutor_id": tutor["id"],
            "start": "2026-12-10T16:00:00Z",
            "end": "2026-12-10T17:00:00Z",
            "timezone": "UTC",
        })
    assert r.status_code == 200, r.text
    new_ref = r.json()["id"]

    # The moved session is already on the draft; a second invoice would double-bill it.
    assert client.post("/invoices/", json={"payer_id": c["id"]}).status_code == 409

    # And the line names the row that now holds the session, not the tombstone — otherwise
    # releasing it frees the wrong booking and leaves the live one claimed but lineless.
    with TestingSessionLocal() as db:
        new_id = db.query(Booking).filter(Booking.public_id == new_ref).first().id
        old = db.query(Booking).filter(Booking.id == b["id"]).first()
        assert old.status == "rescheduled"
        assert old.invoice_id is None
    after = client.get(f"/invoices/{inv['id']}").json()
    assert after["lines"][0]["booking_id"] == new_id


def test_releasing_a_moved_booking_frees_the_live_row(client, link, tutor):
    """Follow-on from the above: release has to reach the row the line now points at."""
    from unittest.mock import MagicMock, patch

    c = _contact(client, "MovedThenFreed")
    b = _booking(link, tutor, c["id"], c["id"], day=10)
    inv = _generate(client)[0]

    svc = MagicMock()
    svc.events().insert().execute.return_value = {"id": "evt-new2"}
    svc.events().delete().execute.return_value = {}
    with patch("routers.bookings.get_calendar_service", return_value=svc):
        moved = client.post(f"/bookings/{b['public_id']}/reschedule", json={
            "tutor_id": tutor["id"],
            "start": "2026-12-11T16:00:00Z",
            "end": "2026-12-11T17:00:00Z",
            "timezone": "UTC",
        }).json()

    r = client.delete(f"/invoices/{inv['id']}/lines/{inv['lines'][0]['id']}")
    assert r.status_code == 200
    assert r.json()["lines"] == []

    # Freed, so the moved session is billable again rather than stranded. Named explicitly, since
    # it now sits in the future and the blanket sweep deliberately stops at now.
    again = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [moved["id"]]})
    assert _amounts(again.json()) == [80]


def test_cancelling_a_series_leaves_draft_lines_alone(client, link, tutor):
    """Same as cancelling one occurrence: the line stands and the admin removes it if it shouldn't.
    The row is hard-deleted, so booking_id nulls out, but the frozen amount survives."""
    from unittest.mock import MagicMock, patch

    c = _contact(client, "SeriesCancelled")
    sid = _series(link, tutor, c["id"], c["id"])
    future = datetime.now(UTC) + timedelta(days=60)
    with TestingSessionLocal() as db:
        occ = Booking(
            public_id=str(uuid4()), tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=c["id"], attendee_id=c["id"], series_id=sid,
            start=future, end=future + timedelta(hours=1),
            google_event_id="evt-series", price_id=link["price_id"],
        )
        db.add(occ)
        db.commit()
        occ_ref = occ.public_id

    # Billed ahead, deliberately — which is the only way a future occurrence gets claimed.
    inv = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [occ_ref]}).json()
    assert _amounts(inv) == [80]

    with TestingSessionLocal() as db:
        series_ref = db.query(BookingSeries).filter(BookingSeries.id == sid).first().public_id

    svc = MagicMock()
    svc.events().patch().execute.return_value = {}
    svc.events().instances().execute.return_value = {"items": []}
    with patch("routers.bookings.get_calendar_service", return_value=svc):
        r = client.delete(f"/bookings/booking-series/{series_ref}")
    assert r.status_code in (200, 204), r.text

    after = client.get(f"/invoices/{inv['id']}").json()
    assert _amounts(after) == [80]
    assert after["lines"][0]["booking_id"] is None   # row gone, line stands


# --- ad-hoc scope ---

def _future_booking(link, tutor, payer_id, attendee_id):
    with TestingSessionLocal() as db:
        start = datetime.now(UTC) + timedelta(days=30)
        b = Booking(
            public_id=str(uuid4()), tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=payer_id, attendee_id=attendee_id, start=start,
            end=start + timedelta(hours=1), google_event_id=f"evt-{uuid4().hex[:8]}",
            price_id=link["price_id"],
        )
        db.add(b)
        db.commit()
        return {"id": b.id, "public_id": b.public_id}


def test_blanket_adhoc_sweep_stops_at_now(client, link, tutor):
    """Unbounded, this billed every occurrence a finite series had materialized — a year ahead —
    and claimed them all, so the engagement read as settled."""
    c = _contact(client, "NotYet")
    _booking(link, tutor, c["id"], c["id"], day=10)     # March, delivered
    _future_booking(link, tutor, c["id"], c["id"])      # a month out

    inv = client.post("/invoices/", json={"payer_id": c["id"]}).json()
    assert _amounts(inv) == [80]        # the delivered one only


def test_a_named_future_booking_still_bills(client, link, tutor):
    """Collecting before the session is the point of picking it by hand."""
    c = _contact(client, "PrePaid")
    future = _future_booking(link, tutor, c["id"], c["id"])

    inv = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [future["public_id"]]}).json()
    assert _amounts(inv) == [80]
    assert inv["lines"][0]["booking_id"] == future["id"]


# --- the link's price round-trips ---

def test_a_links_price_survives_the_round_trip(client, tutor):
    """BookingLink.price_id resolved fine but the response read it through a relationship that
    didn't exist, so every link came back unpriced and the editor loaded blank."""
    schedule = client.post("/schedules", json={
        "tutor_id": tutor["id"], "name": "All", "is_default": True, "timezone": "UTC",
        "days": [{"day_of_week": d, "start_time": "00:00:00", "end_time": "23:59:00"} for d in range(7)],
    }).json()
    body = {
        "slug": f"priced-{uuid4().hex[:6]}", "duration_minutes": 60,
        "price": 80, "price_unit": "per_session",
        "availability": [{"tutor_id": tutor["id"], "schedule_id": schedule["id"]}],
    }
    created = client.post("/booking_links/", json=body).json()
    assert (created["price"], created["price_unit"]) == (80, "per_session")

    fetched = client.get(f"/booking_links/{created['id']}").json()
    assert (fetched["price"], fetched["price_unit"]) == (80, "per_session")


def test_a_price_with_no_unit_is_rejected(client, tutor):
    """What the link editor used to send. 80 says nothing without per-session or per-hour."""
    schedule = client.post("/schedules", json={
        "tutor_id": tutor["id"], "name": "All2", "is_default": True, "timezone": "UTC",
        "days": [{"day_of_week": 0, "start_time": "00:00:00", "end_time": "23:59:00"}],
    }).json()
    r = client.post("/booking_links/", json={
        "slug": "unitless", "duration_minutes": 60, "price": 80,
        "availability": [{"tutor_id": tutor["id"], "schedule_id": schedule["id"]}],
    })
    assert r.status_code == 422


def test_an_enrollments_payer_round_trips(client):
    """payer_id decides who the invoice goes to; the client panel used to drop it from the payload."""
    parent = _contact(client, "Payer")
    kid = _contact(client, "Dependent")
    enrolled = _enroll(client, kid["id"], rate=55, rate_unit="per_session", payer_id=parent["id"])
    assert enrolled["payer_id"] == parent["id"]
    assert client.get(f"/contacts/{kid['id']}").json()["enrollment"]["payer_id"] == parent["id"]


# --- unbilled list and retotalling ---

def test_unbilled_offers_what_else_could_be_billed(client, link, tutor):
    """Feeds the pick-what's-included panel. A future session shows up so billing ahead is a tick."""
    c = _contact(client, "Candidates")
    _booking(link, tutor, c["id"], c["id"], day=5)
    later = _booking(link, tutor, c["id"], c["id"], day=12)
    future = _future_booking(link, tutor, c["id"], c["id"])

    # Draft on one session only, so the other is still unclaimed and on offer.
    inv = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [later["public_id"]]}).json()
    # Added after the draft existed — the case that has no other way onto it.
    item = client.post("/invoice-items/", json={"payer_id": c["id"], "description": "Books", "amount": 25}).json()

    offered = client.get(f"/bookings/?unbilled=true&payer_ids={c['id']}").json()["items"]
    offered_ids = {b["id"] for b in offered}
    assert later["public_id"] not in offered_ids    # already on the invoice
    assert future["public_id"] in offered_ids
    row = next(b for b in offered if b["id"] == future["public_id"])
    assert row["would_bill"] == 80

    pending = client.get(f"/invoice-items/?payer_id={c['id']}&pending=true").json()
    assert [i["id"] for i in pending] == [item["id"]]


def test_unbilled_offers_a_virtual_occurrence_and_materializes_it_on_add(client, link, tutor):
    """The not-yet-materialized future of an indefinite series is real unbilled work — it shows up
    unmaterialized, and only turns into a row at the moment it's actually selected (same
    materialize-on-write-intent rule as reschedule/cancel), not just for being listed."""
    c = _contact(client, "Indefinite")
    sid = _series(link, tutor, c["id"], c["id"])
    now = datetime.now(UTC)

    offered = client.get("/bookings/", params={
        "unbilled": "true", "payer_ids": c["id"],
        "time_min": now.isoformat(), "time_max": (now + timedelta(days=90)).isoformat(),
    }).json()["items"]
    assert len(offered) >= 1
    virtual = offered[0]
    assert ":" in virtual["id"]         # composite ref — not a real row yet
    assert virtual["would_bill"] == 80

    with TestingSessionLocal() as db:
        assert db.query(Booking).filter(Booking.series_id == sid).count() == 0

    inv = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [virtual["id"]]}).json()
    assert _amounts(inv) == [80]
    with TestingSessionLocal() as db:
        # Selecting it is what turned the ref into a row.
        assert db.query(Booking).filter(Booking.series_id == sid, Booking.public_id == virtual["id"]).count() == 1


def test_unticking_recomputes_the_total(client, link, tutor):
    """Deleting a child doesn't drop it from the parent's loaded collection, so summing straight
    after a release left the total at its pre-release figure."""
    c = _contact(client, "Retotal")
    first = _booking(link, tutor, c["id"], c["id"], day=5)
    _booking(link, tutor, c["id"], c["id"], day=12)
    _booking(link, tutor, c["id"], c["id"], day=19)
    inv = _generate(client)[0]
    assert inv["total"] == 240

    r = client.put(f"/invoices/{inv['id']}/lines", json={"booking_ids": [first["public_id"]], "item_ids": []}).json()
    assert len(r["lines"]) == 1
    assert r["total"] == 80

    gone = client.delete(f"/invoices/{inv['id']}/lines/{r['lines'][0]['id']}").json()
    assert gone["lines"] == []
    assert gone["total"] == 0


def test_a_fresh_database_can_still_invoice(client):
    """get_settings used to 500 if nothing had touched /settings/ yet, which took out every route
    needing a timezone — including invoice creation on a new deployment."""
    c = _contact(client, "FreshDb")
    r = client.post("/invoices/", json={"payer_id": c["id"]})
    assert r.status_code == 409   # nothing to bill, but reached the sweep rather than crashing


def test_a_series_with_sub_second_dtstart_can_still_materialize(client, link, tutor):
    """A composite ref carries int(timestamp), so the grid check has to compare at whole seconds.
    Comparing microseconds made every virtual occurrence of such a series unbillable forever."""
    c = _contact(client, "Microseconds")
    now = datetime.now(UTC)
    with TestingSessionLocal() as db:
        s = BookingSeries(
            public_id=str(uuid4()), tutor_id=tutor["id"], booking_link_id=link["id"],
            payer_id=c["id"], attendee_id=c["id"], price_id=link["price_id"],
            # Sub-second precision, as a client posting "…T16:00:00.123456Z" would produce.
            dtstart=(now + timedelta(days=3)).replace(tzinfo=None, microsecond=123456),
            dtend=(now + timedelta(days=3, hours=1)).replace(tzinfo=None, microsecond=123456),
            google_event_id="evt-micro",
        )
        db.add(s)
        db.commit()

    offered = client.get("/bookings/", params={
        "unbilled": "true", "payer_ids": c["id"],
        "time_max": (now + timedelta(days=30)).isoformat(),
    }).json()["items"]
    virtual = next(b for b in offered if ":" in b["id"])

    inv = client.post("/invoices/", json={"payer_id": c["id"], "booking_ids": [virtual["id"]]})
    assert inv.status_code == 201, inv.text
    assert _amounts(inv.json()) == [80]


def test_unbilled_pagination_survives_an_entirely_covered_batch(client, link, tutor):
    """Regression test: a payer whose nearest activity is all covered-by-plan sessions used to
    terminate pagination after one short/empty page, because next_cursor was minted off the
    post-filter survivor count. A real billable booking sitting further out never surfaced.

    booking_amount drops covered rows after the SQL fetch, so capping that fetch at page_size + 1
    can produce a raw batch that filters down to nothing — this reproduces exactly that, with the
    cap set low enough (page_size=2) that the five covered bookings don't fit in one raw batch."""
    payer = _contact(client, "Covered")
    enrollment = _enroll(client, payer["id"], rate=100, rate_unit="per_month")
    series_id = _series(link, tutor, payer["id"], payer["id"], covered_by_enrollment_id=enrollment["id"])
    with TestingSessionLocal() as db:
        # Bounded, not indefinite: a finite covered plan has every occurrence already materialized
        # (see "A bounded series materializes every occurrence at creation" elsewhere), so it
        # contributes nothing through scoped_virtual_occurrences. Keeps this test isolated to the
        # materialized-side fix rather than also exercising the indefinite-series virtual walk.
        db.query(BookingSeries).filter(BookingSeries.id == series_id).update({"count": 10})
        db.commit()

    for day in range(3, 7):  # 4 covered sessions, days 3-6
        _booking(link, tutor, payer["id"], payer["id"], day=day, series_id=series_id,
                  rate_id=enrollment["rate_id"])
    billable = _booking(link, tutor, payer["id"], payer["id"], day=20)  # genuinely billable, later

    params = {"unbilled": "true", "payer_ids": payer["id"], "page_size": 2,
              "time_min": "2026-03-01T00:00:00Z", "time_max": "2026-04-01T00:00:00Z"}
    page1 = client.get("/bookings/", params=params).json()
    assert page1["items"] == []
    assert page1["next_cursor"] is not None  # more to look at, even though this batch served nothing

    page2 = client.get("/bookings/", params={**params, "cursor": page1["next_cursor"]}).json()
    ids = [b["id"] for b in page2["items"]]
    assert billable["public_id"] in ids
    assert page2["next_cursor"] is None  # genuinely exhausted now
