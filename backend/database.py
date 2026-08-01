from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
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
    from models import Settings
    settings = db.query(Settings).filter(Settings.id == 1).first()
    if settings is None:
        raise HTTPException(status_code=500, detail="Settings not initialised")
    return settings

