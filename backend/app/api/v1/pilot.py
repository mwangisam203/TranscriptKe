from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.access import AccessEvent
from app.models.institution import Institution
from app.models.pilot import InstitutionOnboarding, PilotEvaluation
from app.models.user import User, UserRole
from app.schemas.academic import Id
from app.schemas.institution import InstitutionRead
from app.schemas.pilot import (
    EvaluationCreate,
    EvaluationReview,
    InstitutionCreate,
    OnboardingReview,
    OnboardingSubmit,
    OnboardingWrite,
    PilotWindow,
)
from app.services.academic import audit, check_version, lock_institution
from app.services.order_timing import as_utc
from app.services.orders import utcnow
from app.services.pilot import (
    evaluation_snapshot,
    evidence_blockers,
    onboarding_view,
    setup_blockers,
)

router = APIRouter(tags=["pilot evaluation and onboarding"])
STAFF = "/staff/institutions/{institution_id}"
ADMIN = "/admin/institutions/{institution_id}"


def access(db, user, institution_id, *, admin=False, write=False):
    if admin and user.role != UserRole.ADMIN:
        raise HTTPException(403, "Platform administrator access required")
    institution = (
        lock_institution(db, institution_id)
        if write
        else db.get(Institution, institution_id)
    )
    if not institution or not institution.is_active:
        raise HTTPException(404, "Institution not found")
    if not admin:
        require_academic_staff(db, user, institution_id, manage=True)
    return institution


def read_access(
    request: Request,
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    access(
        db, user, institution_id, admin=request.url.path.startswith("/api/v1/admin/")
    )


def fail_blockers(blockers):
    if blockers:
        raise HTTPException(
            409,
            {"message": "Acceptance requirements are incomplete", "blockers": blockers},
        )


def evaluation_view(item):
    return {
        key: as_utc(value)
        if key in ("window_start", "window_end", "created_at", "reviewed_at")
        else value
        for key in (
            "id",
            "institution_id",
            "version",
            "mode",
            "window_start",
            "window_end",
            "evidence",
            "findings",
            "snapshot",
            "created_by",
            "created_at",
            "decision",
            "review_reason",
            "reviewed_by",
            "reviewed_at",
            "review_snapshot",
        )
        for value in [getattr(item, key)]
    }


@router.post("/admin/institutions", response_model=InstitutionRead, status_code=201)
def create_institution(
    payload: InstitutionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Platform administrator access required")
    institution = Institution(**payload.model_dump(), is_active=True, is_approved=False)
    db.add(institution)
    try:
        db.flush()
        db.add(InstitutionOnboarding(institution_id=institution.id))
        audit(db, user.id, institution.id, "onboarding_created", institution.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "An institution with that name or code already exists"
        ) from exc
    db.refresh(institution)
    return institution


@router.get(STAFF + "/onboarding", dependencies=[Depends(read_access)])
@router.get(ADMIN + "/onboarding", dependencies=[Depends(read_access)])
def read_onboarding(institution_id: Id, db: Session = Depends(get_db)):
    result = onboarding_view(db, institution_id)
    events = db.scalars(
        select(AccessEvent)
        .where(
            AccessEvent.institution_id == institution_id,
            AccessEvent.action.in_(
                [
                    "onboarding_created",
                    "onboarding_saved",
                    "onboarding_submitted",
                    "onboarding_reviewed",
                ]
            ),
        )
        .order_by(AccessEvent.id.desc())
        .limit(50)
    ).all()
    result["history"] = [
        {
            "id": e.id,
            "action": e.action,
            "actor_id": e.actor_id,
            "created_at": as_utc(e.created_at),
            "details": e.details,
        }
        for e in events
    ]
    return result


@router.put(STAFF + "/onboarding")
def save_onboarding(
    institution_id: Id,
    payload: OnboardingWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    access(db, user, institution_id, write=True)
    profile = db.get(InstitutionOnboarding, institution_id)
    check_version(profile.version if profile else 0, payload.expected_version)
    if profile and profile.status not in ("draft", "changes_requested"):
        raise HTTPException(409, "Only draft or returned onboarding can be edited")
    if profile is None:
        profile = InstitutionOnboarding(institution_id=institution_id, version=1)
        db.add(profile)
    else:
        profile.version += 1
    profile.status = "draft"
    profile.evidence = payload.evidence.model_dump()
    profile.updated_at = utcnow()
    audit(
        db,
        user.id,
        institution_id,
        "onboarding_saved",
        institution_id,
        version=profile.version,
        evidence=profile.evidence,
    )
    db.commit()
    return onboarding_view(db, institution_id)


@router.post(STAFF + "/onboarding/submit")
def submit_onboarding(
    institution_id: Id,
    payload: OnboardingSubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    access(db, user, institution_id, write=True)
    profile = db.get(InstitutionOnboarding, institution_id)
    if not profile:
        raise HTTPException(409, "Save onboarding evidence first")
    check_version(profile.version, payload.expected_version)
    if profile.status not in ("draft", "changes_requested"):
        raise HTTPException(409, "Onboarding is already submitted or approved")
    fail_blockers(
        evidence_blockers(profile.evidence) + setup_blockers(db, institution_id)
    )
    profile.status, profile.submitted_by = "submitted", user.id
    profile.version += 1
    profile.updated_at = utcnow()
    audit(
        db,
        user.id,
        institution_id,
        "onboarding_submitted",
        institution_id,
        version=profile.version,
    )
    db.commit()
    return onboarding_view(db, institution_id)


@router.post(ADMIN + "/onboarding/review")
def review_onboarding(
    institution_id: Id,
    payload: OnboardingReview,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    access(db, user, institution_id, admin=True, write=True)
    profile = db.get(InstitutionOnboarding, institution_id)
    if not profile:
        raise HTTPException(404, "Onboarding not found")
    check_version(profile.version, payload.expected_version)
    if profile.status != "submitted":
        raise HTTPException(409, "Only submitted onboarding can be reviewed")
    if profile.submitted_by == user.id:
        raise HTTPException(
            403, "A different administrator must review this submission"
        )
    if payload.decision == "approved":
        fail_blockers(
            evidence_blockers(profile.evidence) + setup_blockers(db, institution_id)
        )
    profile.status, profile.reviewed_by, profile.review_reason = (
        payload.decision,
        user.id,
        payload.reason,
    )
    profile.updated_at = utcnow()
    profile.version += 1
    audit(
        db,
        user.id,
        institution_id,
        "onboarding_reviewed",
        institution_id,
        version=profile.version,
        decision=payload.decision,
        reason=payload.reason,
    )
    db.commit()
    return onboarding_view(db, institution_id)


@router.post(STAFF + "/pilot/preview", dependencies=[Depends(read_access)])
@router.post(ADMIN + "/pilot/preview", dependencies=[Depends(read_access)])
def preview(institution_id: Id, payload: PilotWindow, db: Session = Depends(get_db)):
    return evaluation_snapshot(db, institution_id, payload)


@router.get(STAFF + "/pilot/evaluations", dependencies=[Depends(read_access)])
@router.get(ADMIN + "/pilot/evaluations", dependencies=[Depends(read_access)])
def evaluations(
    institution_id: Id,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    scope = PilotEvaluation.institution_id == institution_id
    total = db.scalar(select(func.count(PilotEvaluation.id)).where(scope))
    items = db.scalars(
        select(PilotEvaluation)
        .where(scope)
        .order_by(PilotEvaluation.created_at.desc(), PilotEvaluation.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [evaluation_view(item) for item in items],
    }


@router.post(STAFF + "/pilot/evaluations", status_code=201)
def create_evaluation(
    institution_id: Id,
    payload: EvaluationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    access(db, user, institution_id, write=True)
    fail_blockers(evidence_blockers(payload.evidence.model_dump()))
    item = PilotEvaluation(
        institution_id=institution_id,
        mode=payload.mode,
        window_start=as_utc(payload.window_start),
        window_end=as_utc(payload.window_end),
        evidence=payload.evidence.model_dump(),
        findings=payload.findings,
        snapshot=evaluation_snapshot(db, institution_id, payload),
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    audit(
        db, user.id, institution_id, "pilot_evaluation_created", evaluation_id=item.id
    )
    db.commit()
    return evaluation_view(item)


@router.post(ADMIN + "/pilot/evaluations/{evaluation_id}/review")
def review_evaluation(
    institution_id: Id,
    evaluation_id: str,
    payload: EvaluationReview,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    access(db, user, institution_id, admin=True, write=True)
    item = db.scalar(
        select(PilotEvaluation)
        .where(
            PilotEvaluation.id == evaluation_id,
            PilotEvaluation.institution_id == institution_id,
        )
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Evaluation not found")
    check_version(item.version, payload.expected_version)
    if item.decision != "pending":
        raise HTTPException(
            409, "This evaluation already has a decision; submit a new evaluation"
        )
    if item.created_by == user.id:
        raise HTTPException(
            403, "A different administrator must review this evaluation"
        )
    window = PilotWindow(
        mode=item.mode,
        window_start=as_utc(item.window_start),
        window_end=as_utc(item.window_end),
    )
    current = evaluation_snapshot(db, institution_id, window)
    if payload.decision == "expand":
        fail_blockers(
            item.snapshot["expansion_blockers"] + current["expansion_blockers"]
        )
    item.decision, item.review_reason, item.reviewed_by = (
        payload.decision,
        payload.reason,
        user.id,
    )
    item.reviewed_at, item.review_snapshot = utcnow(), current
    item.version += 1
    audit(
        db,
        user.id,
        institution_id,
        "pilot_evaluation_reviewed",
        evaluation_id=item.id,
        decision=payload.decision,
        reason=payload.reason,
    )
    db.commit()
    return evaluation_view(item)
