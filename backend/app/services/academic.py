from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.academic import (
    AcademicRecordLink,
    InstitutionService,
    OrderingPolicy,
    RecordMatchEvent,
)
from app.models.access import AccessEvent
from app.models.institution import Institution
from app.schemas.academic import RecordSubmission

SUBMISSION_FIELDS = (
    "service_id",
    "admission_number",
    "name_on_record",
    "program",
    "attendance_start_year",
    "attendance_end_year",
    "previous_names",
)


def lock_institution(db: Session, institution_id: int) -> Institution:
    # Configuration, matching and membership writes follow the same institution lock order.
    institution = db.scalar(
        select(Institution)
        .where(Institution.id == institution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if institution is None or not institution.is_active:
        raise HTTPException(404, "Institution not found")
    return institution


def public_institution(db: Session, institution_id: int) -> Institution:
    institution = db.get(Institution, institution_id)
    if institution is None or not institution.is_active or not institution.is_approved:
        raise HTTPException(404, "Institution not found")
    return institution


def check_version(actual: int, expected: int) -> None:
    if actual != expected:
        raise HTTPException(409, "This record changed. Reload it before saving.")


def validate_submission(
    db: Session, institution_id: int, payload: RecordSubmission
) -> dict:
    institution = lock_institution(db, institution_id)
    if not institution.is_approved:
        raise HTTPException(
            409, "This institution is not approved to accept submissions"
        )
    policy = db.get(OrderingPolicy, institution_id)
    if policy is None or not policy.accepting_requests:
        raise HTTPException(409, "This institution is not accepting new submissions")
    service = db.scalar(
        select(InstitutionService).where(
            InstitutionService.id == payload.service_id,
            InstitutionService.institution_id == institution_id,
            InstitutionService.is_active.is_(True),
        )
    )
    if service is None:
        raise HTTPException(422, "Select an active service offered by this institution")
    required = sorted(set(policy.required_fields) | set(service.required_fields))
    missing = []
    for field in required:
        # An explicit [] means the student has no previous names; omission is different.
        if field == "previous_names":
            if field not in payload.model_fields_set:
                missing.append(field)
        elif getattr(payload, field) is None:
            missing.append(field)
    if missing:
        raise HTTPException(
            422,
            {"message": "Required matching information is missing", "fields": missing},
        )
    return {
        "required_fields": required,
        "policy_version": policy.version,
        "service_version": service.version,
        "service_name": service.name,
    }


def submission_snapshot(link: AcademicRecordLink) -> dict:
    return {
        **{field: getattr(link, field) for field in SUBMISSION_FIELDS},
        "requirements": link.requirements_snapshot,
    }


def add_match_event(
    db: Session,
    link: AcademicRecordLink,
    actor_id: int,
    internal_note: str | None = None,
) -> None:
    db.add(
        RecordMatchEvent(
            link_id=link.id,
            actor_id=actor_id,
            version=link.version,
            status=link.status,
            student_message=link.student_message,
            internal_note=internal_note,
            record_reference=link.record_reference,
            submission_snapshot=submission_snapshot(link),
        )
    )


def audit(
    db: Session,
    actor_id: int,
    institution_id: int,
    action: str,
    subject_id: int | None = None,
    **details,
) -> None:
    db.add(
        AccessEvent(
            actor_id=actor_id,
            institution_id=institution_id,
            action=action,
            subject_id=subject_id,
            details=details,
        )
    )
