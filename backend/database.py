from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
from fastapi import Depends
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

class Base(DeclarativeBase):
    pass

SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_settings(db=Depends(get_db)):
    """The singleton, created on first use. Every column has a default, so a fresh deployment has
    working settings rather than a 500 on the first request that needs a timezone."""
    from models import Settings
    settings = db.query(Settings).filter(Settings.id == 1).first()
    if settings is None:
        settings = Settings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

