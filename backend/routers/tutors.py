from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Tutor, Lesson
from schemas import TutorCreate, TutorUpdate, TutorResponse

router = APIRouter(prefix="/tutors", tags=["tutors"])


#todo: add optional ?ids=1,2,3 query param to filter by specific IDs (used in BookingPage to avoid fetching all tutors)
@router.get("/", response_model=list[TutorResponse])
def get_tutors(db: Session = Depends(get_db)):
    return db.query(Tutor).all()


@router.get("/{tutor_id:int}", response_model=TutorResponse)
def get_tutor(tutor_id: int, db: Session = Depends(get_db)):
    tutor = db.query(Tutor).filter(Tutor.id == tutor_id).first()
    if not tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    return tutor


@router.post("/", response_model=TutorResponse, status_code=201)
def create_tutor(tutor_in: TutorCreate, db: Session = Depends(get_db)):
    if db.query(Tutor).filter(Tutor.first_name == tutor_in.first_name, Tutor.last_name == tutor_in.last_name).first():
        raise HTTPException(status_code=409, detail="Tutor with this name already exists")
    new_tutor = Tutor(**tutor_in.model_dump())
    db.add(new_tutor)
    db.commit()
    db.refresh(new_tutor)
    return new_tutor


@router.put("/{tutor_id:int}", response_model=TutorResponse)
def update_tutor(tutor_id: int, tutor_in: TutorUpdate, db: Session = Depends(get_db)):
    db_tutor = db.query(Tutor).filter(Tutor.id == tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    for key, value in tutor_in.model_dump().items():
        setattr(db_tutor, key, value)
    db.commit()
    db.refresh(db_tutor)
    return db_tutor


@router.delete("/{tutor_id:int}", response_model=TutorResponse)
def delete_tutor(tutor_id: int, db: Session = Depends(get_db)):
    db_tutor = db.query(Tutor).filter(Tutor.id == tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    if db.query(Lesson).filter(Lesson.tutor_id == tutor_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete tutor with existing lessons")
    db.delete(db_tutor)
    db.commit()
    return db_tutor
