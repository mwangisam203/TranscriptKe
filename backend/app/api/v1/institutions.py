from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.institution import Institution
from app.schemas.institution import InstitutionRead

router = APIRouter(prefix="/institutions", tags=["institutions"])


@router.get("", response_model=list[InstitutionRead])
def list_institutions(
    db: Session = Depends(get_db), q: str = Query(default="", max_length=100)
):
    statement = (
        select(Institution)
        .where(Institution.is_active.is_(True))
        .where(Institution.is_approved.is_(True))
        .order_by(Institution.name)
    )
    for term in q.split():
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            or_(
                Institution.name.ilike(f"%{escaped}%", escape="\\"),
                Institution.code.ilike(f"%{escaped}%", escape="\\"),
            )
        )

    return db.scalars(statement).all()


@router.get("/{institution_id}", response_model=InstitutionRead)
def read_institution(institution_id: int, db: Session = Depends(get_db)):
    institution = db.get(Institution, institution_id)
    if institution is None or not institution.is_active or not institution.is_approved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Institution not found",
        )

    return institution
