from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.academic import AcademicRecordLink, MatchStatus, RecordMatchEvent
from app.models.user import User
from app.schemas.academic import (
    Id,
    IdentityRead,
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
    audit,
    check_version,
    lock_institution,
    validate_submission,
)
from app.services.identity import identity_fields, reveal_identity
from app.services.identity_images import attach_images, decrypt_image, prepare_image
from app.services.order_attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentScanner,
    get_attachment_scanner,
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
        AcademicRecordLink.checkout_required.is_(False),
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    link = db.scalar(statement)
    if link is None:
        raise HTTPException(404, "Academic record link not found")
    return link


def save_link(
    payload: RecordCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    *,
    images=None,
):
    if payload.identity_document_type and not images:
        raise HTTPException(422, "Provide both front and back ID images")
    requirements = validate_submission(
        db, payload.institution_id, payload, has_identity_images=bool(images)
    )
    link = AcademicRecordLink(
        user_id=user.id,
        checkout_required=settings.PAYMENT_COLLECTION_POLICY == "before_review",
        requirements_snapshot=requirements,
        status=MatchStatus.PENDING.value,
        **payload.model_dump(
            mode="json", exclude={"id_number", "identity_document_type"}
        ),
        **identity_fields(payload.id_number, user.id, payload.institution_id),
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "You already have a link for this institution and identifier"
        ) from exc
    if images:
        attach_images(link, payload.identity_document_type, images)
        audit(
            db,
            user.id,
            link.institution_id,
            "record_identity_images_submitted",
            subject_id=link.id,
            document_type=payload.identity_document_type,
        )
    add_match_event(db, link, user.id)
    db.commit()
    db.refresh(link)
    return link


@router.post("/me/academic-record-links", response_model=RecordRead, status_code=201)
def create_link(
    payload: RecordCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return save_link(payload, db, user)


def parse_image_submission(raw, schema):
    if len(raw) > 16_384:
        raise HTTPException(413, "Personal details are too large")
    try:
        return schema.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {**error, "loc": ("body", "details", *error["loc"])}
                for error in exc.errors()
            ]
        ) from exc


def read_image_pair(payload, front, back, scanner):
    if not payload.identity_document_type or not front or not back:
        raise HTTPException(
            422, "Choose ID or driving licence and upload both front and back"
        )
    images = {}
    for side, upload in (("front", front), ("back", back)):
        images[side] = prepare_image(
            upload.filename, upload.file.read(MAX_ATTACHMENT_BYTES + 1), scanner
        )
    front_digest = images["front"].pop("content_digest")
    back_digest = images["back"].pop("content_digest")
    if front_digest == back_digest:
        raise HTTPException(
            422,
            "Front and back must be different photographs of the two sides of your document",
        )
    return images


@router.post(
    "/me/academic-record-submissions", response_model=RecordRead, status_code=201
)
def create_link_with_images(
    details: str = Form(..., max_length=16_384),
    front: UploadFile = File(...),
    back: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    scanner: AttachmentScanner = Depends(get_attachment_scanner),
):
    payload = parse_image_submission(details, RecordCreate)
    # Authorize the institution and service before decoding uploads.
    validate_submission(db, payload.institution_id, payload, has_identity_images=True)
    images = read_image_pair(payload, front, back, scanner)
    return save_link(payload, db, user, images=images)


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


def save_resubmission(
    link_id: Id,
    payload: RecordResubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    *,
    images=None,
):
    link = own_link(db, user, link_id)
    if (
        payload.identity_document_type
        and not images
        and payload.identity_document_type != link.identity_document_type
    ):
        raise HTTPException(
            422, "Changing the identity document requires both replacement images"
        )
    requirements = validate_submission(
        db,
        link.institution_id,
        payload,
        has_identity_images=bool(images) or len(link.identity_images) == 2,
    )
    db.refresh(link, with_for_update=True)
    check_version(link.version, payload.expected_version)
    if link.status not in (MatchStatus.NEEDS_INFORMATION, MatchStatus.REJECTED):
        raise HTTPException(
            409,
            "Only links requiring information or previously rejected can be resubmitted",
        )
    for field in SUBMISSION_FIELDS:
        setattr(link, field, getattr(payload, field))
    for field, value in identity_fields(
        payload.id_number, user.id, link.institution_id
    ).items():
        setattr(link, field, value)
    if images:
        # Flush removals before inserting the replacements for each unique side.
        link.identity_images.clear()
        db.flush()
        attach_images(link, payload.identity_document_type, images)
        audit(
            db,
            user.id,
            link.institution_id,
            "record_identity_images_replaced",
            subject_id=link.id,
            document_type=payload.identity_document_type,
        )
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
            409, "You already have a link for this institution and identifier"
        ) from exc
    add_match_event(db, link, user.id)
    db.commit()
    db.refresh(link)
    return link


@router.post(
    "/me/academic-record-links/{link_id}/resubmissions", response_model=RecordRead
)
def resubmit_link(
    link_id: Id,
    payload: RecordResubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return save_resubmission(link_id, payload, db, user)


@router.post(
    "/me/academic-record-links/{link_id}/resubmissions-with-images",
    response_model=RecordRead,
)
def resubmit_link_with_images(
    link_id: Id,
    details: str = Form(..., max_length=16_384),
    front: UploadFile = File(...),
    back: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    scanner: AttachmentScanner = Depends(get_attachment_scanner),
):
    link = own_link(db, user, link_id)
    payload = parse_image_submission(details, RecordResubmit)
    validate_submission(db, link.institution_id, payload, has_identity_images=True)
    db.refresh(link, with_for_update=True)
    check_version(link.version, payload.expected_version)
    if link.status not in (MatchStatus.NEEDS_INFORMATION, MatchStatus.REJECTED):
        raise HTTPException(
            409, "Only links needing information or rejected may be resubmitted"
        )
    images = read_image_pair(payload, front, back, scanner)
    return save_resubmission(link_id, payload, db, user, images=images)


def image_response(db, link, side, user):
    image = next((image for image in link.identity_images if image.side == side), None)
    if image is None:
        raise HTTPException(404, "Identity image not found")
    data = decrypt_image(image)
    filename = f"{image.document_type}-{side}.jpg"
    audit(
        db,
        user.id,
        link.institution_id,
        "record_identity_image_downloaded",
        subject_id=link.id,
        side=side,
    )
    db.commit()
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get("/me/academic-record-links/{link_id}/identity-images/{side}")
def download_my_identity_image(
    link_id: Id,
    side: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return image_response(db, own_link(db, user, link_id), side, user)


@router.get(
    "/staff/institutions/{institution_id}/record-matches/{link_id}/identity-images/{side}"
)
def download_staff_identity_image(
    institution_id: Id,
    link_id: Id,
    side: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return image_response(db, staff_link(db, user, institution_id, link_id), side, user)


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
        AcademicRecordLink.institution_id == institution_id,
        AcademicRecordLink.checkout_required.is_(False),
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
    "/staff/institutions/{institution_id}/record-matches/{link_id}/identity",
    response_model=IdentityRead,
)
def read_identity(
    institution_id: Id,
    link_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    link = staff_link(db, user, institution_id, link_id)
    if not link.identity_ciphertext:
        raise HTTPException(404, "No ID number was provided")
    raw = reveal_identity(link.identity_ciphertext)
    audit(db, user.id, institution_id, "record_identity_viewed", subject_id=link.id)
    db.commit()
    return IdentityRead(id_number=raw)


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


@router.get(
    "/staff/institutions/{institution_id}/record-matches/{link_id}/personal-details"
)
def matching_personal_details(
    institution_id: Id,
    link_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    from app.models.profile import UserProfile
    from app.services.profiles import read_details

    link = staff_link(db, user, institution_id, link_id)
    details = read_details(db.get(UserProfile, link.user_id))
    if details is None:
        raise HTTPException(404, "No personal profile was provided")
    audit(
        db,
        user.id,
        institution_id,
        "record_personal_details_viewed",
        subject_id=link.id,
    )
    db.commit()
    return {"name": details.full_name, "date_of_birth": details.date_of_birth}
