import hashlib
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.orders import OrderItem
from app.models.user import User
from app.schemas.academic import Id
from app.schemas.issuance import DownloadInput, IssueInput, RecipientEmail, RevokeInput
from app.schemas.orders import VersionInput
from app.services.academic import check_version
from app.services.fulfillment import (
    actionable,
    assigned_case,
    preparation_blockers,
    record,
)
from app.services.issuance import (
    access_code,
    document_for,
    documents,
    download,
    locked_delivery,
    notify_delivery,
    public_document,
    release_blockers,
)
from app.services.mail import get_mailer
from app.services.order_attachments import (
    MAX_ATTACHMENT_BYTES,
    get_attachment_scanner,
    validate_attachment,
)
from app.services.orders import get_order, rows, utcnow
from app.services.throttle import enforce_rate_limit

router = APIRouter(tags=["issuance and delivery"])
STAFF = "/staff/institutions/{institution_id}/orders/{order_id}/documents"


def overview(db, order, staff=False):
    return {
        "version": order.version,
        "mode": settings.ISSUANCE_MODE,
        "blockers": release_blockers(db, order) if staff else [],
        "documents": [
            public_document(db, d, staff=staff) for d in documents(db, order)
        ],
    }


@router.get("/orders/{order_id}/documents")
def student_documents(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return overview(db, get_order(db, user, order_id))


@router.get(STAFF)
def staff_documents(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return overview(
        db, get_order(db, user, order_id, institution_id=institution_id), True
    )


@router.post(STAFF, status_code=201)
def upload_document(
    institution_id: Id,
    order_id: Id,
    expected_version: int = Form(..., ge=1),
    item_key: str = Form(..., max_length=40),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    scanner=Depends(get_attachment_scanner),
):
    order = actionable(db, user, institution_id, order_id, expected_version)
    assigned_case(db, user, order)
    if not settings.ISSUANCE_ENABLED:
        raise HTTPException(409, "Issuance is disabled")
    item = next((i for i in rows(db, OrderItem, order.id) if i.key == item_key), None)
    if not item or item.fulfillment_status != "ready":
        raise HTTPException(409, "Choose a document marked ready by the registrar")
    if preparation_blockers(db, order):
        raise HTTPException(
            409, "Resolve registrar preparation blockers before uploading"
        )
    recipient = next(
        r
        for r in order.submitted_snapshot["recipients"]
        if r["key"] == item.recipient_key
    )
    if recipient["delivery_method"] != "secure_electronic":
        raise HTTPException(409, "Only secure electronic delivery is implemented")
    if db.scalar(
        select(IssuedDocument.id).where(IssuedDocument.active_item_id == item.id)
    ):
        raise HTTPException(
            409, "Revoke the existing document before uploading a replacement"
        )
    data = file.file.read(MAX_ATTACHMENT_BYTES + 1)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(422, "Institution-prepared documents must be PDF files")
    validate_attachment(file.filename, data, scanner)
    if not data.rstrip().endswith(b"%%EOF"):
        raise HTTPException(422, "PDF must have a complete end-of-file marker")
    doc = IssuedDocument(
        order_id=order.id,
        item_id=item.id,
        active_item_id=item.id,
        mode=settings.ISSUANCE_MODE,
        filename=file.filename,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        data=data,
        uploaded_by=user.id,
    )
    db.add(doc)
    record(
        db,
        order,
        user,
        "document_uploaded",
        "The institution prepared a document for release.",
        f"Scanned PDF for item {item.key}; SHA-256 {doc.sha256}.",
    )
    db.commit()
    return overview(db, order, True)


def pdf_response(data, doc):
    prefix = "DEMO-" if doc.mode == "demo" else ""
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{prefix}document-{doc.id}.pdf"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Referrer-Policy": "no-referrer",
            "X-Document-SHA256": doc.sha256,
        },
    )


@router.get(STAFF + "/{document_id}/preview")
def preview(
    institution_id: Id,
    order_id: Id,
    document_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    if order.user_id == user.id:
        raise HTTPException(403, "Another registrar must handle your document")
    doc = document_for(db, order, document_id)
    data = doc.data
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        raise HTTPException(409, "Document integrity check failed")
    record(
        db,
        order,
        user,
        "document_previewed",
        "An institutional reviewer accessed the prepared document.",
        f"Previewed document {doc.id}.",
    )
    db.commit()
    return pdf_response(data, doc)


@router.post(STAFF + "/{document_id}/issue")
def issue(
    institution_id: Id,
    order_id: Id,
    document_id: UUID,
    payload: IssueInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = actionable(db, user, institution_id, order_id, payload.expected_version)
    assigned_case(db, user, order)
    doc = document_for(db, order, document_id)
    blockers = release_blockers(db, order)
    if blockers:
        raise HTTPException(
            409, {"message": "Resolve release blockers", "fields": blockers}
        )
    if not payload.attested:
        raise HTTPException(
            422, "Attest that the document and recipient match the authorized request"
        )
    if doc.issued_at or doc.revoked_at or doc.mode != settings.ISSUANCE_MODE:
        raise HTTPException(
            409, "Document cannot be issued in its current state or mode"
        )
    item = db.get(OrderItem, doc.item_id)
    if item.fulfillment_status != "ready":
        raise HTTPException(409, "Document item is not ready")
    recipient = next(
        r
        for r in order.submitted_snapshot["recipients"]
        if r["key"] == item.recipient_key
    )
    if recipient["delivery_method"] != "secure_electronic" or not recipient["email"]:
        raise HTTPException(
            409, "Secure electronic delivery with a recipient email is required"
        )
    if hashlib.sha256(doc.data).hexdigest() != doc.sha256:
        raise HTTPException(409, "Document integrity check failed")
    doc.issued_at, doc.issued_by = utcnow(), user.id
    item.fulfillment_status = "issued"
    db.add(
        DocumentDelivery(
            document_id=doc.id,
            order_id=order.id,
            recipient_email=recipient["email"],
            expires_at=utcnow() + timedelta(days=settings.DELIVERY_EXPIRE_DAYS),
        )
    )
    record(
        db,
        order,
        user,
        "document_issued",
        f"The institution released a {doc.mode} document for secure recipient delivery.",
        payload.internal_note,
    )
    db.commit()
    return overview(db, order, True)


@router.post(STAFF + "/{document_id}/revoke")
def revoke(
    institution_id: Id,
    order_id: Id,
    document_id: UUID,
    payload: RevokeInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    require_academic_staff(db, user, institution_id, manage=True)
    check_version(order.version, payload.expected_version)
    if user.id == order.user_id:
        raise HTTPException(403, "Another manager must revoke your document")
    doc = document_for(db, order, document_id)
    if doc.revoked_at:
        raise HTTPException(409, "Document already revoked")
    doc.revoked_at, doc.revoked_by, doc.revocation_reason = (
        utcnow(),
        user.id,
        payload.reason,
    )
    doc.active_item_id = None
    item = db.get(OrderItem, doc.item_id)
    if item.fulfillment_status != "cancelled":
        item.fulfillment_status = "processing"
    delivery = db.scalar(
        select(DocumentDelivery).where(DocumentDelivery.document_id == doc.id)
    )
    if delivery:
        delivery.code_hash = None
        delivery.code_expires_at = None
    record(
        db,
        order,
        user,
        "document_revoked",
        payload.reason,
        f"Revoked document {doc.id}; previously downloaded copies cannot be recalled.",
    )
    db.commit()
    return overview(db, order, True)


@router.post(STAFF + "/{document_id}/notify")
def notify(
    request: Request,
    institution_id: Id,
    order_id: Id,
    document_id: UUID,
    payload: VersionInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    mailer=Depends(get_mailer),
):
    enforce_rate_limit(db, request, "document_notify", str(user.id), account_limit=20)
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    check_version(order.version, payload.expected_version)
    if order.user_id == user.id:
        raise HTTPException(403, "Another registrar must handle your delivery")
    doc = document_for(db, order, document_id)
    delivery = db.scalar(
        select(DocumentDelivery).where(DocumentDelivery.document_id == doc.id)
    )
    if not delivery:
        raise HTTPException(409, "Issue the document before notifying its recipient")
    notify_delivery(db, order, doc, delivery, mailer)
    return overview(db, order, True)


@router.post("/deliveries/{delivery_id}/access-codes", status_code=202)
def request_code(
    request: Request,
    delivery_id: UUID,
    payload: RecipientEmail,
    db: Session = Depends(get_db),
    mailer=Depends(get_mailer),
):
    enforce_rate_limit(
        db, request, "document_code", str(delivery_id), account_limit=5, ip_limit=30
    )
    try:
        order, doc, delivery = locked_delivery(db, delivery_id)
        access_code(db, order, doc, delivery, str(payload.email), mailer)
    except HTTPException as exc:
        if exc.status_code not in (404, 410):
            raise
    return {
        "message": "If this delivery is available and the email matches, an access code will be sent."
    }


@router.post("/deliveries/{delivery_id}/download")
def recipient_download(
    request: Request,
    delivery_id: UUID,
    payload: DownloadInput,
    db: Session = Depends(get_db),
):
    enforce_rate_limit(
        db,
        request,
        "document_download",
        str(delivery_id),
        account_limit=20,
        ip_limit=60,
    )
    order, doc, delivery = locked_delivery(db, delivery_id)
    return pdf_response(download(db, order, doc, delivery, payload.code), doc)
