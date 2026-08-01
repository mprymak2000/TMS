# this has been scrapped and integrated into evenetypes as fields

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import CancellationPolicy
from schemas import CancellationPolicyCreate, CancellationPolicyUpdate, CancellationPolicyResponse

router = APIRouter(prefix="/cancellation-policies", tags=["cancellation-policies"])

@router.get("/", response_model=list[CancellationPolicyResponse])
def get_cancellation_policies(db: Session = Depends(get_db)):
    return db.query(CancellationPolicy).all()

@router.get("/{policy_id}", response_model=CancellationPolicyResponse)
def get_cancellation_policy(policy_id: int, db: Session = Depends(get_db)):
    policy = db.query(CancellationPolicy).filter(CancellationPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Cancellation policy not found")
    return policy

@router.post("/", response_model=CancellationPolicyResponse, status_code=201)
def create_cancellation_policy(policy_in: CancellationPolicyCreate, db: Session = Depends(get_db)):
    existing = db.query(CancellationPolicy).filter(CancellationPolicy.name == policy_in.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Cancellation policy with this name already exists")
    policy = CancellationPolicy(**policy_in.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy

@router.put("/{policy_id}", response_model=CancellationPolicyResponse)
def update_cancellation_policy(policy_id: int, policy_in: CancellationPolicyUpdate, db: Session = Depends(get_db)):
    policy = db.query(CancellationPolicy).filter(CancellationPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Cancellation policy not found")
    existing = db.query(CancellationPolicy).filter(CancellationPolicy.name == policy_in.name).first()
    if existing and existing.id != policy_id:
        raise HTTPException(status_code=409, detail="Cancellation policy with this name already exists")
    for field, value in policy_in.model_dump().items():
        setattr(policy, field, value)
    db.commit()
    db.refresh(policy)
    return policy

@router.delete("/{policy_id}", response_model=CancellationPolicyResponse)
def delete_cancellation_policy(policy_id: int, db: Session = Depends(get_db)):
    policy = db.query(CancellationPolicy).filter(CancellationPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Cancellation policy not found")
    db.delete(policy)
    db.commit()
    return policy