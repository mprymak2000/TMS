from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Schedule, ScheduleDay, Tutor
from schemas import ScheduleCreate, ScheduleUpdate, ScheduleResponse

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("/", response_model=list[ScheduleResponse])
def get_schedules(db: Session = Depends(get_db)):
    return db.query(Schedule).all()
    

@router.get("/{schedule_id:int}", response_model=ScheduleResponse)
def get_schedule_by_id(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule entry not found.")
    return schedule


@router.post("/", response_model=ScheduleResponse, status_code=201)
def create_schedule(schedule_in: ScheduleCreate, db: Session = Depends(get_db)):
    db_tutor = db.query(Tutor).filter(Tutor.id == schedule_in.tutor_id).first()
    if not db_tutor:
        raise HTTPException(status_code=404, detail="Tutor not found.")

    if db.query(Schedule).filter(Schedule.tutor_id == schedule_in.tutor_id, Schedule.name == schedule_in.name).first():
        raise HTTPException(status_code=409, detail="A schedule with this name already exists for this tutor.")

    # if a new schedule being made is set as default, set all other schedules to NOT default
    if schedule_in.is_default:
        db.query(Schedule).filter(
            Schedule.tutor_id == schedule_in.tutor_id,
            Schedule.is_default == True
        ).update({"is_default": False})

    new_schedule = Schedule(**schedule_in.model_dump(exclude={"days"}))
    db.add(new_schedule)
    db.flush()

    # for list of dayCreates passed in, iterate each pydantic dayCreate, unpack and add to ScheduleDay DB model, linking days to this schedule 
    for day in schedule_in.days:
        db.add(ScheduleDay(schedule_id=new_schedule.id, **day.model_dump()))

    db.commit()
    db.refresh(new_schedule)
    return new_schedule


@router.put("/{schedule_id:int}", response_model=ScheduleResponse)
def update_schedule(schedule_id: int, schedule_update: ScheduleUpdate, db: Session = Depends(get_db)):
    db_schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule entry not found.")

    conflict = db.query(Schedule).filter(
        Schedule.tutor_id == db_schedule.tutor_id,
        Schedule.name == schedule_update.name,
        Schedule.id != schedule_id,
    ).first()
    if conflict:
        raise HTTPException(status_code=409, detail="A schedule with this name already exists for this tutor.")

    if db_schedule.is_default is True and schedule_update.is_default is False: # true -> false
        raise HTTPException(status_code=400, detail="Cannot unset default from the current default schedule without setting another schedule to default.")

    if db_schedule.is_default is False and schedule_update.is_default is True: # false -> true
        # set all other schedules for this tutor to not default
        db.query(Schedule).filter(
            Schedule.tutor_id == db_schedule.tutor_id,
            Schedule.is_default,
        ).update({"is_default": False})

    for key, value in schedule_update.model_dump(exclude={"days"}).items():
        setattr(db_schedule, key, value)

    for day in db_schedule.days:
        db.delete(day)
    for day in schedule_update.days:
        db.add(ScheduleDay(schedule_id=db_schedule.id, **day.model_dump()))

    db.commit()
    db.refresh(db_schedule)
    return db_schedule


@router.delete("/{schedule_id:int}", response_model=ScheduleResponse)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    db_schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    if db_schedule.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default schedule. Set another schedule as default first.")

    db.delete(db_schedule)
    db.commit()
    return db_schedule