from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.academic import InstitutionService, OrderingPolicy
from app.models.institution import Institution
from app.models.user import User, UserRole
from app.schemas.academic import (
    ApprovalWrite,
    Id,
    PolicyRead,
    PolicyWrite,
    ServiceRead,
    ServiceUpdate,
    ServiceWrite,
)
from app.schemas.institution import InstitutionRead
from app.services.academic import (
    audit,
    check_version,
    lock_institution,
    public_institution,
)

router = APIRouter(tags=["institution catalog"])


def policy_or_default(db: Session, institution_id: int):
    return db.get(OrderingPolicy, institution_id) or PolicyRead(
        institution_id=institution_id,
        accepting_requests=False,
        student_instructions="",
        required_fields=[],
        version=0,
    )


@router.get("/institutions/{institution_id}/services", response_model=list[ServiceRead])
def public_services(institution_id: Id, db: Session = Depends(get_db)):
    public_institution(db, institution_id)
    return db.scalars(
        select(InstitutionService)
        .where(
            InstitutionService.institution_id == institution_id,
            InstitutionService.is_active.is_(True),
        )
        .order_by(InstitutionService.name, InstitutionService.id)
    ).all()


@router.get("/institutions/{institution_id}/ordering-policy", response_model=PolicyRead)
def public_policy(institution_id: Id, db: Session = Depends(get_db)):
    public_institution(db, institution_id)
    return policy_or_default(db, institution_id)


@router.get("/staff/institutions/{institution_id}", response_model=InstitutionRead)
def staff_institution(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id)
    return db.get(Institution, institution_id)


@router.get(
    "/staff/institutions/{institution_id}/services", response_model=list[ServiceRead]
)
def staff_services(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id)
    return db.scalars(
        select(InstitutionService)
        .where(InstitutionService.institution_id == institution_id)
        .order_by(InstitutionService.id)
    ).all()


@router.post(
    "/staff/institutions/{institution_id}/services",
    response_model=ServiceRead,
    status_code=201,
)
def create_service(
    institution_id: Id,
    payload: ServiceWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    lock_institution(db, institution_id)
    require_academic_staff(db, user, institution_id, manage=True)
    service = InstitutionService(
        institution_id=institution_id, **payload.model_dump(mode="json")
    )
    db.add(service)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "This institution already has a service with that code"
        ) from exc
    audit(
        db,
        user.id,
        institution_id,
        "service_created",
        service.id,
        configuration=payload.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(service)
    return service


@router.put(
    "/staff/institutions/{institution_id}/services/{service_id}",
    response_model=ServiceRead,
)
def update_service(
    institution_id: Id,
    service_id: Id,
    payload: ServiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    lock_institution(db, institution_id)
    require_academic_staff(db, user, institution_id, manage=True)
    service = db.scalar(
        select(InstitutionService).where(
            InstitutionService.id == service_id,
            InstitutionService.institution_id == institution_id,
        )
    )
    if service is None:
        raise HTTPException(404, "Service not found")
    check_version(service.version, payload.expected_version)
    before = ServiceRead.model_validate(service).model_dump(mode="json")
    for field, value in payload.model_dump(
        mode="json", exclude={"expected_version"}
    ).items():
        setattr(service, field, value)
    service.version += 1
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "This institution already has a service with that code"
        ) from exc
    audit(
        db,
        user.id,
        institution_id,
        "service_updated",
        service.id,
        before=before,
        after=ServiceRead.model_validate(service).model_dump(mode="json"),
    )
    db.commit()
    db.refresh(service)
    return service


@router.get(
    "/staff/institutions/{institution_id}/ordering-policy", response_model=PolicyRead
)
def staff_policy(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id)
    return policy_or_default(db, institution_id)


@router.put(
    "/staff/institutions/{institution_id}/ordering-policy", response_model=PolicyRead
)
def update_policy(
    institution_id: Id,
    payload: PolicyWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    lock_institution(db, institution_id)
    require_academic_staff(db, user, institution_id, manage=True)
    policy = db.get(OrderingPolicy, institution_id)
    check_version(policy.version if policy else 0, payload.expected_version)
    before = PolicyRead.model_validate(
        policy_or_default(db, institution_id)
    ).model_dump(mode="json")
    if policy is None:
        policy = OrderingPolicy(institution_id=institution_id, version=1)
        db.add(policy)
    else:
        policy.version += 1
    for field, value in payload.model_dump(
        mode="json", exclude={"expected_version"}
    ).items():
        setattr(policy, field, value)
    audit(
        db,
        user.id,
        institution_id,
        "ordering_policy_updated",
        institution_id,
        before=before,
        after=payload.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/admin/institutions", response_model=list[InstitutionRead])
def admin_institutions(
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = Query(0, ge=0),
):
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Platform administrator access required")
    return db.scalars(
        select(Institution).order_by(Institution.id).offset(offset).limit(100)
    ).all()


@router.put(
    "/admin/institutions/{institution_id}/approval", response_model=InstitutionRead
)
def approve_institution(
    institution_id: Id,
    payload: ApprovalWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Platform administrator access required")
    institution = lock_institution(db, institution_id)
    if institution.is_approved != payload.expected_approved:
        raise HTTPException(409, "Institution approval changed. Reload before saving.")
    institution.is_approved = payload.approved
    audit(
        db,
        user.id,
        institution_id,
        "institution_approval_changed",
        institution_id,
        approved=payload.approved,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(institution)
    return institution
