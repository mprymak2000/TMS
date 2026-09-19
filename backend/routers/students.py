from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Contact, Student, Lesson
from schemas import StudentCreate, StudentUpdate, StudentResponse

router = APIRouter(prefix="/students", tags=["students"])


@router.get("/", response_model=list[StudentResponse])
def get_students(db: Session = Depends(get_db)):
    return db.query(Student).all()


@router.get("/{student_id:int}", response_model=StudentResponse)
def get_student(student_id: int, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.post("/", response_model=StudentResponse, status_code=201)
def create_student(student_in: StudentCreate, db: Session = Depends(get_db)):
    """Enroll an existing contact. Identity already exists; this only adds the billing relationship."""
    if not db.query(Contact).filter(Contact.id == student_in.contact_id).first():
        raise HTTPException(status_code=404, detail="Contact not found")
    if db.query(Student).filter(Student.contact_id == student_in.contact_id).first():
        raise HTTPException(status_code=409, detail="Contact is already enrolled")
    new_student = Student(**student_in.model_dump())
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    return new_student


@router.put("/{student_id:int}", response_model=StudentResponse)
def update_student(student_id: int, student_in: StudentUpdate, db: Session = Depends(get_db)):
    db_student = db.query(Student).filter(Student.id == student_id).first()
    if not db_student:
        raise HTTPException(status_code=404, detail="Student not found")
    for key, value in student_in.model_dump().items():
        setattr(db_student, key, value)
    db.commit()
    db.refresh(db_student)
    return db_student


@router.delete("/{student_id:int}", response_model=StudentResponse)
def delete_student(student_id: int, db: Session = Depends(get_db)):
    db_student = db.query(Student).filter(Student.id == student_id).first()
    if not db_student:
        raise HTTPException(status_code=404, detail="Student not found")
    if db.query(Lesson).filter(Lesson.student_id == student_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete student with existing lessons")
    # Serialized before the delete: the response nests the contact, and the relationship can't be
    # loaded off a row that's gone.
    response = StudentResponse.model_validate(db_student)
    db.delete(db_student)
    db.commit()
    return response
