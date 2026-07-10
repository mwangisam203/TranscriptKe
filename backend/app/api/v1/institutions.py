from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.institution import Institution
from app.schemas.institution import InstitutionRead


router = APIRouter(prefix="/institutions", tags=["institutions"])


@router.get("", response_model=list[InstitutionRead])
def list_institutions(db: Session = Depends(get_db)):
    statement = (
        select(Institution)
        .where(Institution.is_active.is_(True))
        .order_by(Institution.name)
    )

    return db.scalars(statement).all()


@router.get("/{institution_id}", response_model=InstitutionRead)
def read_institution(institution_id: int, db: Session = Depends(get_db)):
    institution = db.get(Institution, institution_id)
    if institution is None or not institution.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Institution not found",
        )

    return institution
