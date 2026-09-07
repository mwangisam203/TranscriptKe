from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.access import InstitutionMembership, MembershipRole
from app.models.institution import Institution
from app.models.user import User, UserRole


def require_membership(
    db: Session, user: User, institution_id: int, *, manage: bool = False
) -> InstitutionMembership | None:
    institution = db.get(Institution, institution_id)
    if institution is None or not institution.is_active:
        raise HTTPException(404, "Institution not found")
    if not user.is_email_verified:
        raise HTTPException(403, "Verify your email before continuing")
    # This override is for membership administration only, not future academic records access.
    if user.role == UserRole.ADMIN:
        return None
    membership = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.institution_id == institution_id,
            InstitutionMembership.user_id == user.id,
            InstitutionMembership.is_active.is_(True),
        )
    )
    if membership is None or (manage and membership.role != MembershipRole.MANAGER):
        raise HTTPException(403, "You do not have permission for this institution")
    return membership


def require_invitation_permission(
    db: Session, user: User, institution_id: int, role: MembershipRole
) -> None:
    require_membership(db, user, institution_id, manage=True)
    if role == MembershipRole.MANAGER and user.role != UserRole.ADMIN:
        raise HTTPException(
            403, "Only platform administrators can appoint institution managers"
        )
