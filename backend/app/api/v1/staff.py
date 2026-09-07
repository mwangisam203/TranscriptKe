from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import require_invitation_permission, require_membership
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.access import (
    AccessEvent,
    ActionToken,
    InstitutionMembership,
    MembershipRole,
)
from app.models.institution import Institution
from app.models.user import User, UserRole
from app.schemas.auth import TokenConfirmation
from app.schemas.staff import InvitationCreate, InvitationRead, MembershipRead
from app.services.mail import Mailer, get_mailer
from app.services.throttle import enforce_rate_limit
from app.services.tokens import consume_token, find_token, issue_token, utcnow

router = APIRouter(prefix="/staff", tags=["staff access"])


@router.get("/memberships", response_model=list[MembershipRead])
def my_memberships(
    db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return db.scalars(
        select(InstitutionMembership)
        .join(Institution)
        .where(
            InstitutionMembership.user_id == user.id,
            InstitutionMembership.is_active.is_(True),
            Institution.is_active.is_(True),
        )
        .order_by(InstitutionMembership.id)
    ).all()


@router.get(
    "/institutions/{institution_id}/members", response_model=list[MembershipRead]
)
def list_members(
    institution_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = 0,
):
    require_membership(db, user, institution_id)
    if offset < 0:
        raise HTTPException(422, "Offset must be nonnegative")
    return db.scalars(
        select(InstitutionMembership)
        .where(
            InstitutionMembership.institution_id == institution_id,
            InstitutionMembership.is_active.is_(True),
        )
        .order_by(InstitutionMembership.id)
        .offset(offset)
        .limit(100)
    ).all()


@router.post(
    "/institutions/{institution_id}/invitations",
    response_model=InvitationRead,
    status_code=201,
)
def invite_staff(
    institution_id: int,
    payload: InvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    mailer: Mailer = Depends(get_mailer),
):
    require_invitation_permission(db, user, institution_id, payload.role)
    enforce_rate_limit(
        db, request, "staff_invitation", payload.email, account_limit=5, ip_limit=30
    )
    # Serialize membership administration for this institution, including invitation acceptance.
    db.scalar(
        select(Institution).where(Institution.id == institution_id).with_for_update()
    )
    require_invitation_permission(db, user, institution_id, payload.role)
    existing = db.scalar(
        select(InstitutionMembership)
        .join(User)
        .where(
            InstitutionMembership.institution_id == institution_id,
            InstitutionMembership.is_active.is_(True),
            User.email == payload.email,
        )
    )
    if existing:
        raise HTTPException(409, "This account is already an active member")
    record = issue_token(
        db,
        mailer,
        purpose="staff_invitation",
        email=payload.email,
        minutes=settings.INVITATION_EXPIRE_MINUTES,
        institution_id=institution_id,
        invited_by=user.id,
        membership_role=payload.role.value,
    )
    db.add(
        AccessEvent(
            actor_id=user.id,
            institution_id=institution_id,
            action="staff_invited",
            subject_id=record.id,
        )
    )
    db.commit()
    db.refresh(record)
    return record


@router.get(
    "/institutions/{institution_id}/invitations", response_model=list[InvitationRead]
)
def list_invitations(
    institution_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = 0,
):
    require_membership(db, user, institution_id, manage=True)
    if offset < 0:
        raise HTTPException(422, "Offset must be nonnegative")
    return db.scalars(
        select(ActionToken)
        .where(
            ActionToken.purpose == "staff_invitation",
            ActionToken.institution_id == institution_id,
            ActionToken.consumed_at.is_(None),
            ActionToken.expires_at > utcnow(),
        )
        .order_by(ActionToken.id)
        .offset(offset)
        .limit(100)
    ).all()


@router.post("/invitations/accept", response_model=MembershipRead)
def accept_invitation(
    payload: TokenConfirmation,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    enforce_rate_limit(db, request, "accept_invitation", user.email)
    record = find_token(db, payload.token, "staff_invitation")
    if record.email != user.email:
        raise HTTPException(403, "This invitation belongs to another account")
    db.scalar(
        select(Institution)
        .where(Institution.id == record.institution_id)
        .with_for_update()
    )
    inviter = db.get(User, record.invited_by)
    if inviter is None:
        raise HTTPException(403, "This invitation is no longer authorized")
    # An invitation loses authority when its issuer's membership is revoked.
    require_invitation_permission(
        db, inviter, record.institution_id, MembershipRole(record.membership_role)
    )
    membership = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.user_id == user.id,
            InstitutionMembership.institution_id == record.institution_id,
        )
    )
    if membership and membership.is_active:
        raise HTTPException(409, "You are already an active member")
    consume_token(db, record)
    if membership is None:
        membership = InstitutionMembership(
            user_id=user.id,
            institution_id=record.institution_id,
            role=MembershipRole(record.membership_role),
        )
        db.add(membership)
    else:
        membership.is_active = True
        membership.role = MembershipRole(record.membership_role)
    if user.role == UserRole.STUDENT:
        user.role = UserRole.INSTITUTION_STAFF
    db.flush()
    db.add(
        AccessEvent(
            actor_id=user.id,
            institution_id=record.institution_id,
            action="invitation_accepted",
            subject_id=membership.id,
        )
    )
    db.commit()
    db.refresh(membership)
    return membership


@router.delete(
    "/institutions/{institution_id}/invitations/{invitation_id}", status_code=204
)
def revoke_invitation(
    institution_id: int,
    invitation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    db.scalar(
        select(Institution).where(Institution.id == institution_id).with_for_update()
    )
    require_membership(db, user, institution_id, manage=True)
    record = db.scalar(
        select(ActionToken).where(
            ActionToken.id == invitation_id,
            ActionToken.institution_id == institution_id,
            ActionToken.purpose == "staff_invitation",
        )
    )
    if record is None:
        raise HTTPException(404, "Invitation not found")
    require_invitation_permission(
        db, user, institution_id, MembershipRole(record.membership_role)
    )
    if record.consumed_at is None:
        record.consumed_at = utcnow()
        db.add(
            AccessEvent(
                actor_id=user.id,
                institution_id=institution_id,
                action="invitation_revoked",
                subject_id=record.id,
            )
        )
    db.commit()
    return Response(status_code=204)


@router.delete(
    "/institutions/{institution_id}/members/{membership_id}", status_code=204
)
def revoke_membership(
    institution_id: int,
    membership_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    db.scalar(
        select(Institution).where(Institution.id == institution_id).with_for_update()
    )
    require_membership(db, user, institution_id, manage=True)
    member = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.id == membership_id,
            InstitutionMembership.institution_id == institution_id,
        )
    )
    if member is None:
        raise HTTPException(404, "Membership not found")
    require_invitation_permission(db, user, institution_id, member.role)
    member.is_active = False
    # Invalidate outstanding invitations to this member, so old codes cannot restore access.
    target = db.get(User, member.user_id)
    for invitation in db.scalars(
        select(ActionToken).where(
            ActionToken.institution_id == institution_id,
            ActionToken.purpose == "staff_invitation",
            ActionToken.consumed_at.is_(None),
            (ActionToken.email == target.email) | (ActionToken.invited_by == target.id),
        )
    ):
        invitation.consumed_at = utcnow()
    db.add(
        AccessEvent(
            actor_id=user.id,
            institution_id=institution_id,
            action="membership_revoked",
            subject_id=member.id,
        )
    )
    db.commit()
    return Response(status_code=204)
