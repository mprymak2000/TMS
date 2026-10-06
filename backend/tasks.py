import logging
import os
import procrastinate
from sqlalchemy import and_
from sqlalchemy.orm import joinedload
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from database import SessionLocal
from models import Booking, BookingSeries, Contact, Enrollment, Lesson, Settings
from billing import draft_invoice_for_payer, money, payers_with_activity
from booking_utils import _ensure_occurrence, active_series_filter, indefinite_series_filter, is_series_active, series_step

# Strip SQLAlchemy dialect prefix (+psycopg2) — psycopg3 expects plain postgresql://
_dsn = os.getenv("DATABASE_URL", "").replace("+psycopg2", "")

# App is the central Procrastinate registry.
# connector: PsycopgConnector is async psycopg3 — required for running the worker.
#   (SQLAlchemyPsycopg2Connector is the alternative for deferring jobs inside FastAPI
#    transactions, but can't run the worker. We'll add that later for emails.)
# import_paths: tells the worker where to find task definitions on startup.
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=_dsn),
    import_paths=["tasks"],
)


# @app.task registers the function as a Procrastinate task.
# @app.periodic wraps it with a cron schedule — the worker inserts a job row at 2am daily.
# timestamp: Unix timestamp of when the job was scheduled, injected by Procrastinate.
#
# DESIGN: polling chosen over chained triggers for simplicity.
# extend_all_series (cron) fans out one extend_single_series job per active indefinite series.
# Each job is independently tracked and retried by Procrastinate — full visibility per series.
# Steady state: 1 upcoming row per series.
#
# FUTURE — chained trigger (more elegant, revisit at scale):
#   After _ensure_occurrence creates row N, defer a job to fire at row N's end time
#   (run_at=booking.end). That job creates row N+1 and defers itself again. Wrap both
#   ops in one DB transaction via SQLAlchemyPsycopg2Connector so occurrence exists ↔
#   next link is scheduled (transactional outbox). Keep this polling job as safety net.
@app.periodic(cron="0 2 * * *")
@app.task
def extend_all_series(timestamp: int):
    """Daily: fan out one extend_single_series job per active indefinite series."""
    db = SessionLocal()
    try:
        settings = db.query(Settings).filter(Settings.id == 1).first()
        if settings is None:
            raise RuntimeError("Settings row not found")
        today = datetime.now(ZoneInfo(settings.business_timezone)).date()
        series_list = db.query(BookingSeries).filter(
            active_series_filter(),
            indefinite_series_filter(),
        ).all()
        for series in series_list:
            # .defer() inserts a row into procrastinate_jobs — the worker picks it up and
            # calls extend_single_series(series_id=...) in a separate execution.
            extend_single_series.defer(series_id=series.id)
    except Exception:
        logging.exception("extend_all_series failed")
        raise
    finally:
        db.close()


# @app.task without @app.periodic — not scheduled directly, only deferred by extend_all_series.
# series_id: injected by Procrastinate from the job row kwargs.
@app.task
def extend_single_series(series_id: int):
    """Ensure a single active indefinite series has exactly one upcoming confirmed occurrence."""
    db = SessionLocal()
    try:
        settings = db.query(Settings).filter(Settings.id == 1).first()
        if settings is None:
            raise RuntimeError("Settings row not found")
        tz = ZoneInfo(settings.business_timezone)

        series = db.query(BookingSeries).filter(BookingSeries.id == series_id).first()
        if series is None:
            logging.warning(f"extend_single_series: series {series_id} not found")
            return
        # Enqueued when the series was active - may have been cancelled/closed since (a real
        # race, since jobs sit queued before a worker picks them up). Expected, not an error:
        # return cleanly rather than let _ensure_occurrence's ValueError trigger a retry loop
        # that would just hit the same permanent state forever.
        today = datetime.now(tz).date()
        if not is_series_active(series, db):
            return
        # Same race as above, different cause: the series' tutor may have gone inactive since this
        # job was enqueued. Don't attach new future occurrences to a tutor no longer taking them.
        if not series.tutor.is_active:
            return

        has_future = (
            db.query(Booking)
            .filter(
                Booking.series_id == series.id,
                Booking.status == "confirmed",
                Booking.start > datetime.now(UTC),
            )
            .first()
        )
        if has_future:
            return

        latest = (
            db.query(Booking)
            .filter(Booking.series_id == series.id)
            .order_by(Booking.start.desc())
            .first()
        )
        if latest is None:
            return

        next_date = latest.start.astimezone(tz).date() + series_step(series)
        next_start_utc = datetime.combine(next_date, series.dtstart.time(), tzinfo=tz).astimezone(UTC)
        _ensure_occurrence(series, next_start_utc, db, settings)
        db.commit()
    except Exception:
        logging.exception(f"extend_single_series: failed for series {series_id}")
        db.rollback()
        raise
    finally:
        db.close()


@app.periodic(cron="0 6 * * 0")
@app.task
def draft_lessons(timestamp: int):
    """Sunday 6am: create draft Lesson rows for past confirmed bookings that have no lesson yet."""
    # TODO: review before enabling — verify fee/payout defaults and idempotency
    db = SessionLocal()
    try:
        settings = db.query(Settings).filter(Settings.id == 1).first()
        if settings is None:
            logging.error("draft_lessons: Settings row not found")
            return
        tz = ZoneInfo(settings.business_timezone)

        # Only bookings whose attendee has an open enrollment can produce a lesson — everyone else
        # has no rate to bill at.
        bookings = (
            db.query(Booking)
            .join(Enrollment, and_(
                Enrollment.contact_id == Booking.attendee_id,
                Enrollment.ended_on.is_(None),
            ))
            # The join filters but doesn't populate, so preload what the loop walks — otherwise it's
            # three lazy queries per booking.
            .options(
                joinedload(Booking.attendee).selectinload(Contact.enrollments),
                joinedload(Booking.tutor),
            )
            .filter(
                Booking.status == "confirmed",
                Booking.start <= datetime.now(UTC),
                ~Booking.lesson.has(),
            )
            .all()
        )

        created = 0
        for booking in bookings:
            hrs = (booking.end - booking.start).total_seconds() / 3600
            enrollment = booking.attendee.current_enrollment
            # Hourly only — invoicing bills clients, so skip rather than record hrs x a monthly plan.
            if enrollment.rate is None or enrollment.rate.unit != "per_hour":
                continue
            tutor = booking.tutor
            created += 1
            db.add(Lesson(
                booking_id=booking.id,
                enrollment_id=enrollment.id,
                tutor_id=booking.tutor_id,
                date=booking.start.astimezone(tz).date(),
                hrs=hrs,
                fee=money(hrs) * enrollment.rate.amount,
                tutor_payout=money(hrs) * tutor.pay_rate,
            ))
        db.commit()
        logging.info(f"draft_lessons: created {created} of {len(bookings)} candidate booking(s)")
    except Exception:
        logging.exception("draft_lessons failed")
        db.rollback()
        raise
    finally:
        db.close()


@app.periodic(cron="0 3 1 * *")
@app.task
def draft_invoices(timestamp: int):
    """3am on the 1st: fan out one drafting job per payer with activity last month.

    Fanned out rather than looped inline for the same reason as extend_all_series: one payer whose
    invoice fails shouldn't block everyone after them, and Procrastinate retries each independently.
    A payer not invoiced is money not collected, so that isolation matters more here than anywhere.

    Drafts only — nothing is sent, and finalizing stays a human action.
    """
    db = SessionLocal()
    try:
        settings = db.query(Settings).filter(Settings.id == 1).first()
        if settings is None:
            logging.error("draft_invoices: Settings row not found")
            return
        if not settings.billing_automation_enabled:
            logging.info("draft_invoices: billing automation disabled, skipping")
            return
        # First of this month back to first of last, in business time.
        period_end = datetime.now(ZoneInfo(settings.business_timezone)).date().replace(day=1)
        period_start = (period_end - timedelta(days=1)).replace(day=1)

        payer_ids = payers_with_activity(db, period_start, period_end, settings)
        for payer_id in payer_ids:
            # Dates as ISO strings — job kwargs have to be JSON-serializable.
            draft_invoice_for_one.defer(
                payer_id=payer_id,
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
            )
        logging.info(f"draft_invoices: deferred {len(payer_ids)} payer(s) for {period_start}")
    except Exception:
        logging.exception("draft_invoices failed")
        raise
    finally:
        db.close()


@app.task
def draft_invoice_for_one(payer_id: int, period_start: str, period_end: str):
    """One payer's draft. Safe to retry: the overlap guard makes it a no-op once theirs exists."""
    db = SessionLocal()
    try:
        settings = db.query(Settings).filter(Settings.id == 1).first()
        if settings is None:
            raise RuntimeError("Settings row not found")

        invoice = draft_invoice_for_payer(
            db, payer_id, settings, date.fromisoformat(period_start), date.fromisoformat(period_end),
        )
        # None means nothing outstanding, or they already have an invoice covering this period.
        # Both are expected, so return cleanly rather than trigger a retry that can't succeed.
        if invoice is None:
            logging.info(f"draft_invoice_for_one: nothing to draft for payer {payer_id}")
            return
        logging.info(f"draft_invoice_for_one: drafted {invoice.public_id} for payer {payer_id}")
    except Exception:
        logging.exception(f"draft_invoice_for_one: failed for payer {payer_id}")
        db.rollback()
        raise
    finally:
        db.close()
