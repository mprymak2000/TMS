import logging
import os
import procrastinate
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from database import SessionLocal
from models import Booking, BookingSeries, Lesson, Settings
from booking_utils import _ensure_occurrence, active_series_filter

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
            active_series_filter(today),
            BookingSeries.until == None,
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

        next_date = latest.start.astimezone(tz).date() + timedelta(weeks=1)
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
    # TODO: review before enabling — verify fee/payout defaults, student_id null handling, and idempotency
    db = SessionLocal()
    try:
        settings = db.query(Settings).filter(Settings.id == 1).first()
        if settings is None:
            logging.error("draft_lessons: Settings row not found")
            return
        tz = ZoneInfo(settings.business_timezone)

        bookings = (
            db.query(Booking)
            .filter(
                Booking.status == "confirmed",
                Booking.start <= datetime.now(UTC),
                Booking.student_id != None,
                ~Booking.lesson.has(),
            )
            .all()
        )

        for booking in bookings:
            hrs = (booking.end - booking.start).total_seconds() / 3600
            student = booking.student_record
            tutor = booking.tutor
            db.add(Lesson(
                booking_id=booking.id,
                student_id=booking.student_id,
                tutor_id=booking.tutor_id,
                date=booking.start.astimezone(tz).date(),
                hrs=hrs,
                fee=hrs * student.rate,
                tutor_payout=hrs * tutor.pay_rate,
            ))
        db.commit()
        logging.info(f"draft_lessons: created {len(bookings)} draft lesson(s)")
    except Exception:
        logging.exception("draft_lessons failed")
        db.rollback()
        raise
    finally:
        db.close()
