from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from booking_utils import active_series_filter
from database import get_db, get_settings as settings_singleton
import models
import schemas

router = APIRouter(prefix="/settings", tags=["settings"])


# TODO: replace get_or_create with a proper first-run setup flow (onboarding wizard / seed script)
# Same behaviour as database.get_settings, which every route needing a timezone depends on.
def get_or_create_settings(db: Session) -> models.Settings:
    return settings_singleton(db)


def _timezone_locked(db: Session) -> bool:
    """Whether any still-running series anchors to the business zone. See update_settings."""
    return db.query(models.BookingSeries).filter(active_series_filter()).first() is not None


def _response(settings: models.Settings, db: Session) -> schemas.SettingsResponse:
    response = schemas.SettingsResponse.model_validate(settings)
    response.timezone_locked = _timezone_locked(db)
    return response


@router.get("/", response_model=schemas.SettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    return _response(get_or_create_settings(db), db)


@router.put("/", response_model=schemas.SettingsResponse)
def update_settings(settings_in: schemas.SettingsUpdate, db: Session = Depends(get_db)):
    try:
        ZoneInfo(settings_in.business_timezone)
    except (ValueError, KeyError, ZoneInfoNotFoundError):
        raise HTTPException(status_code=422, detail=f"Invalid timezone: {settings_in.business_timezone}")

    settings = get_or_create_settings(db)

    # The business zone is a setup-time decision, not a live toggle, once a series exists.
    # BookingSeries.dtstart is a naive business-local wall clock while Booking.start is absolute UTC,
    # and this column is the only thing tying the two together. Changing it moves the rule's grid off
    # the rows already materialized from it, which (verified empirically) duplicates occurrences in
    # every listing, makes every emailed manage link for a not-yet-materialized occurrence fail the
    # grid check in _ensure_occurrence, and leaves Google holding the old zone on the RRULE event.
    #
    # Not auto-shifted, because two opposite intents are indistinguishable here: correcting a
    # mislabelled zone means re-anchoring every start from dtstart, while relocating the business
    # leaves it genuinely ambiguous whether a 10am client stays 10am or keeps their instant. So this
    # rejects rather than guesses. Scoped to *active* series: a cancelled one generates nothing and
    # resolves no refs, so it shifts as harmlessly as a standalone booking does.
    if settings_in.business_timezone != settings.business_timezone and _timezone_locked(db):
        raise HTTPException(
            status_code=409,
            detail=(
                "Can't change the business timezone while recurring series are still running — their "
                "session times are anchored to it. Cancel them first, or keep the current zone."
            ),
        )

    settings.business_timezone = settings_in.business_timezone
    settings.billing_automation_enabled = settings_in.billing_automation_enabled
    db.commit()
    db.refresh(settings)
    return _response(settings, db)
