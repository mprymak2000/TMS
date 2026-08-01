from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas

router = APIRouter(prefix="/settings", tags=["settings"])


# TODO: replace get_or_create with a proper first-run setup flow (onboarding wizard / seed script)
def get_or_create_settings(db: Session) -> models.Settings:
    settings = db.query(models.Settings).filter(models.Settings.id == 1).first()
    if not settings:
        settings = models.Settings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("/", response_model=schemas.SettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    return get_or_create_settings(db)


@router.put("/", response_model=schemas.SettingsResponse)
def update_settings(settings_in: schemas.SettingsUpdate, db: Session = Depends(get_db)):
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        ZoneInfo(settings_in.business_timezone)
    except (KeyError, Exception):
        raise HTTPException(status_code=422, detail=f"Invalid timezone: {settings_in.business_timezone}")

    settings = get_or_create_settings(db)
    settings.business_timezone = settings_in.business_timezone
    db.commit()
    db.refresh(settings)
    return settings
