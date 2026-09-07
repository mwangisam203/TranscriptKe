from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.academic import AcademicRecordLink, MatchStatus, RecordMatchEvent
from app.models.user import User
from app.schemas.academic import (
    Id,
    MatchDecision,
    RecordCreate,
    RecordEventRead,
    RecordRead,
    RecordResubmit,
    StaffRecordEventRead,
    StaffRecordRead,
)
from app.services.academic import (
    SUBMISSION_FIELDS,
    add_match_event,
    check_version,
    lock_institution,
    validate_submission,
)
from app.services.tokens import utcnow

router = APIRouter(tags=["academic record matching"])


def own_link(db: Session, user: User, link_id: int) -> AcademicRecordLink:
    link = db.scalar(
        select(AcademicRecordLink).where(
            AcademicRecordLink.id == link_id, AcademicRecordLink.user_id == user.id
        )
    )
    if link is None:
        raise HTTPException(404, "Academic record link not found")
    return link


def staff_link(
    db: Session, user: User, institution_id: int, link_id: int, *, lock: bool = False
) -> AcademicRecordLink:
    require_academic_staff(db, user, institution_id)
    statement = select(AcademicRecordLink).where(
        AcademicRecordLink.id == link_id,
        AcademicRecordLink.institution_id == institution_id,
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    link = db.scalar(statement)
    if link is None:
        raise HTTPException(404, "Academic record link not found")
    return link


@router.post("/me/academic-record-links", response_model=RecordRead, status_code=201)
def create_link(
    payload: RecordCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    requirements = validate_submission(db, payload.institution_id, payload)
    link = AcademicRecordLink(
        user_id=user.id,
        requirements_snapshot=requirements,
        status=MatchStatus.PENDING.value,
        **payload.model_dump(mode="json"),
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "You already have a link for this institution and admission number"
        ) from exc
    add_match_event(db, link, user.id)
    db.commit()
    db.refresh(link)
    return link


@router.get("/me/academic-record-links", response_model=list[RecordRead])
def list_my_links(
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return db.scalars(
        select(AcademicRecordLink)
        .where(AcademicRecordLink.user_id == user.id)
        .order_by(AcademicRecordLink.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/me/academic-record-links/{link_id}", response_model=RecordRead)
def read_my_link(
    link_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return own_link(db, user, link_id)


@router.get(
    "/me/academic-record-links/{link_id}/events", response_model=list[RecordEventRead]
)
def my_link_events(
    link_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    own_link(db, user, link_id)
    return db.scalars(
        select(RecordMatchEvent)
        .where(RecordMatchEvent.link_id == link_id)
        .order_by(RecordMatchEvent.version)
    ).all()


@router.post(
    "/me/academic-record-links/{link_id}/resubmissions", response_model=RecordRead
)
def resubmit_link(
    link_id: Id,
    payload: RecordResubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    link = own_link(db, user, link_id)
    requirements = validate_submission(db, link.institution_id, payload)
    db.refresh(link, with_for_update=True)
    check_version(link.version, payload.expected_version)
    if link.status not in (MatchStatus.NEEDS_INFORMATION, MatchStatus.REJECTED):
        raise HTTPException(
            409,
            "Only links requiring information or previously rejected can be resubmitted",
        )
    for field in SUBMISSION_FIELDS:
        setattr(link, field, getattr(payload, field))
    link.requirements_snapshot = requirements
    link.status = MatchStatus.PENDING.value
    link.student_message = None
    link.record_reference = None
    link.version += 1
    link.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "You already have a link for this institution and admission number"
        ) from exc
    add_match_event(db, link, user.id)
    db.commit()
    db.refresh(link)
    return link


@router.get(
    "/staff/institutions/{institution_id}/record-matches",
    response_model=list[StaffRecordRead],
)
def list_matches(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    status: MatchStatus | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    require_academic_staff(db, user, institution_id)
    statement = select(AcademicRecordLink).where(
        AcademicRecordLink.institution_id == institution_id
    )
    if status:
        statement = statement.where(AcademicRecordLink.status == status.value)
    return db.scalars(
        statement.order_by(AcademicRecordLink.updated_at, AcademicRecordLink.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get(
    "/staff/institutions/{institution_id}/record-matches/{link_id}",
    response_model=StaffRecordRead,
)
def read_match(
    institution_id: Id,
    link_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return staff_link(db, user, institution_id, link_id)


@router.get(
    "/staff/institutions/{institution_id}/record-matches/{link_id}/events",
    response_model=list[StaffRecordEventRead],
)
def match_events(
    institution_id: Id,
    link_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    staff_link(db, user, institution_id, link_id)
    return db.scalars(
        select(RecordMatchEvent)
        .where(RecordMatchEvent.link_id == link_id)
        .order_by(RecordMatchEvent.version)
    ).all()


@router.post(
    "/staff/institutions/{institution_id}/record-matches/{link_id}/decisions",
    response_model=StaffRecordRead,
)
def decide_match(
    institution_id: Id,
    link_id: Id,
    payload: MatchDecision,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    institution = lock_institution(db, institution_id)
    link = staff_link(db, user, institution_id, link_id, lock=True)
    if link.user_id == user.id:
        raise HTTPException(
            403, "Another staff member must review your own academic record"
        )
    check_version(link.version, payload.expected_version)
    if link.status != MatchStatus.PENDING:
        # Correct an earlier decision without deleting its evidence or silently rematching it.
        if (
            link.status not in (MatchStatus.MATCHED, MatchStatus.REJECTED)
            or payload.decision != "needs_information"
        ):
            raise HTTPException(409, "This link is not awaiting a decision")
        require_academic_staff(db, user, institution_id, manage=True)
    if payload.decision == "matched" and not institution.is_approved:
        raise HTTPException(
            409, "Institution approval is required before confirming records"
        )
    link.status = payload.decision
    link.student_message = payload.student_message
    link.record_reference = payload.record_reference
    link.version += 1
    link.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409,
            "This institutional record is already linked; review the ownership conflict",
        ) from exc
    add_match_event(db, link, user.id, payload.internal_note)
    db.commit()
    db.refresh(link)
    return link
