import hashlib
from typing import Annotated
from urllib.parse import quote as urlquote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.academic import AcademicRecordLink
from app.models.orders import (
    Order,
    OrderAttachment,
    OrderCancellation,
    OrderConsent,
    OrderEvent,
    OrderMessage,
)
from app.models.user import User
from app.schemas.academic import Id
from app.schemas.orders import (
    CancellationDecision,
    CancellationInput,
    ConsentInput,
    ConsentRead,
    DraftInput,
    EventRead,
    ItemMutation,
    MessageInput,
    MessageRead,
    OrderCreate,
    OrderRead,
    QuoteRead,
    RecipientMutation,
    StaffMessageInput,
    SubmitInput,
    VersionInput,
)
from app.services.academic import check_version, lock_institution
from app.services.order_attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    AttachmentScanner,
    get_attachment_scanner,
    validate_attachment,
)
from app.services.orders import (
    CONSENT_TEXT,
    CONSENT_VERSION,
    can_cancel,
    digest,
    draft_data,
    draft_only,
    event,
    get_order,
    make_quote,
    read_order,
    replace_draft,
    rows,
    submit_order,
    utcnow,
    valid_quote,
)

router = APIRouter(tags=["transcript orders"])
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]


def new_order(db, user, link_id, key, source=None):
    link = db.scalar(
        select(AcademicRecordLink).where(
            AcademicRecordLink.id == link_id, AcademicRecordLink.user_id == user.id
        )
    )
    if link is None:
        raise HTTPException(404, "Academic record link not found")
    # Serializing by the account also protects retries that accidentally select a different institution.
    institution = lock_institution(db, link.institution_id)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    fingerprint = digest(
        {
            "academic_record_link_id": link_id,
            "source_order_id": source.id if source else None,
        }
    )
    existing = db.scalar(
        select(Order).where(Order.user_id == user.id, Order.creation_key == key)
    )
    if existing:
        if existing.creation_hash != fingerprint:
            raise HTTPException(
                409, "This idempotency key was already used for another draft"
            )
        return existing
    if not institution.is_approved:
        raise HTTPException(409, "Institution approval is required to create an order")
    order = Order(
        user_id=user.id,
        institution_id=link.institution_id,
        academic_record_link_id=link.id,
        creation_key=key,
        creation_hash=fingerprint,
        status="draft",
        version=1,
    )
    db.add(order)
    db.flush()
    event(db, order, user.id, "draft_created", "Draft order created.", bump=False)
    if source:
        data = draft_data(db, source)
        data["expected_version"] = 1
        replace_draft(db, order, user, DraftInput(**data))
    db.flush()
    return order


@router.get("/orders/consent-text")
def consent_text():
    return {"text_version": CONSENT_VERSION, "text": CONSENT_TEXT}


@router.get("/orders/attachment-policy")
def attachment_policy():
    return {
        "max_bytes": MAX_ATTACHMENT_BYTES,
        "max_files": MAX_ATTACHMENTS,
        "extensions": [".txt", ".pdf", ".png", ".jpg", ".jpeg"]
        if settings.ATTACHMENT_SCANNER == "clamav"
        else [".txt"],
    }


@router.post("/orders", response_model=OrderRead, status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = new_order(db, user, payload.academic_record_link_id, idempotency_key)
    db.commit()
    return read_order(db, order)


@router.get("/orders", response_model=list[OrderRead])
def list_orders(
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    return [
        read_order(db, order)
        for order in db.scalars(
            select(Order)
            .where(Order.user_id == user.id)
            .order_by(Order.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/orders/{order_id}", response_model=OrderRead)
def read_own_order(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return read_order(db, get_order(db, user, order_id))


@router.put("/orders/{order_id}", response_model=OrderRead)
def update_order(
    order_id: Id,
    payload: DraftInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    replace_draft(db, order, user, payload)
    db.commit()
    return read_order(db, order)


def mutate_component(
    db, user, order_id, collection, key, expected_version, values=None, *, create=False
):
    order = get_order(db, user, order_id, lock=True)
    draft_only(order)
    check_version(order.version, expected_version)
    data = draft_data(db, order)
    existing = next((entry for entry in data[collection] if entry["key"] == key), None)
    if create and existing:
        raise HTTPException(409, "This draft component key already exists")
    if not create and existing is None:
        raise HTTPException(404, "Draft component not found")
    data[collection] = [entry for entry in data[collection] if entry["key"] != key]
    if values is not None:
        data[collection].append(values)
    try:
        payload = DraftInput(**data)
    except ValidationError as exc:
        raise HTTPException(
            422,
            "Invalid draft: check recipient references, duplicate items and item limits",
        ) from exc
    replace_draft(db, order, user, payload)
    db.commit()
    return read_order(db, order)


@router.post("/orders/{order_id}/recipients", response_model=OrderRead)
def add_recipient(
    order_id: Id,
    payload: RecipientMutation,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return mutate_component(
        db,
        user,
        order_id,
        "recipients",
        payload.key,
        payload.expected_version,
        payload.model_dump(mode="json", exclude={"expected_version"}),
        create=True,
    )


@router.put("/orders/{order_id}/recipients/{key}", response_model=OrderRead)
def edit_recipient(
    order_id: Id,
    key: str,
    payload: RecipientMutation,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    if key != payload.key:
        raise HTTPException(422, "Recipient keys cannot be changed")
    return mutate_component(
        db,
        user,
        order_id,
        "recipients",
        key,
        payload.expected_version,
        payload.model_dump(mode="json", exclude={"expected_version"}),
    )


@router.delete("/orders/{order_id}/recipients/{key}", response_model=OrderRead)
def remove_recipient(
    order_id: Id,
    key: str,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return mutate_component(db, user, order_id, "recipients", key, expected_version)


@router.post("/orders/{order_id}/items", response_model=OrderRead)
def add_item(
    order_id: Id,
    payload: ItemMutation,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return mutate_component(
        db,
        user,
        order_id,
        "items",
        payload.key,
        payload.expected_version,
        payload.model_dump(mode="json", exclude={"expected_version"}),
        create=True,
    )


@router.put("/orders/{order_id}/items/{key}", response_model=OrderRead)
def edit_item(
    order_id: Id,
    key: str,
    payload: ItemMutation,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    if key != payload.key:
        raise HTTPException(422, "Item keys cannot be changed")
    return mutate_component(
        db,
        user,
        order_id,
        "items",
        key,
        payload.expected_version,
        payload.model_dump(mode="json", exclude={"expected_version"}),
    )


@router.delete("/orders/{order_id}/items/{key}", response_model=OrderRead)
def remove_item(
    order_id: Id,
    key: str,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return mutate_component(db, user, order_id, "items", key, expected_version)


@router.post("/orders/{order_id}/validation")
def validate_order(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    from app.services.orders import build_snapshot

    order = get_order(db, user, order_id, lock=True)
    draft_only(order)
    snapshot = build_snapshot(db, order)
    return {
        "valid": True,
        "total_minor": snapshot["total_minor"],
        "currency": snapshot["currency"],
    }


@router.post("/orders/{order_id}/quotes", response_model=QuoteRead, status_code=201)
def quote_order(
    order_id: Id,
    payload: VersionInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    check_version(order.version, payload.expected_version)
    quote = make_quote(db, order)
    db.commit()
    return quote


@router.post("/orders/{order_id}/consents", response_model=ConsentRead)
def accept_consent(
    order_id: Id,
    payload: ConsentInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    draft_only(order)
    quote = valid_quote(db, order, payload.quote_id)
    if payload.text_version != CONSENT_VERSION:
        raise HTTPException(409, "Review the current consent wording")
    consent = db.scalar(select(OrderConsent).where(OrderConsent.quote_id == quote.id))
    if consent is None:
        consent = OrderConsent(
            order_id=order.id,
            quote_id=quote.id,
            user_id=user.id,
            text_version=CONSENT_VERSION,
            text=CONSENT_TEXT,
            scope_hash=quote.scope_hash,
        )
        db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent


@router.get("/orders/{order_id}/consents", response_model=list[ConsentRead])
def read_consents(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    get_order(db, user, order_id)
    return rows(db, OrderConsent, order_id)


@router.post("/orders/{order_id}/submit", response_model=OrderRead)
def submit(
    order_id: Id,
    payload: SubmitInput,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    submit_order(db, order, user, payload, idempotency_key)
    db.commit()
    return read_order(db, order)


@router.post("/orders/{order_id}/reorder", response_model=OrderRead, status_code=201)
def reorder(
    order_id: Id,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    source = get_order(db, user, order_id, lock=True)
    if source.status == "draft":
        raise HTTPException(
            409, "Use the existing draft before creating a repeat order"
        )
    order = new_order(db, user, source.academic_record_link_id, idempotency_key, source)
    db.commit()
    return read_order(db, order)


@router.post("/orders/{order_id}/attachments", response_model=OrderRead)
def attach_file(
    order_id: Id,
    expected_version: int = Form(ge=1),
    file: UploadFile = File(),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    scanner: AttachmentScanner = Depends(get_attachment_scanner),
):
    order = get_order(db, user, order_id, lock=True)
    draft_only(order)
    check_version(order.version, expected_version)
    if len(rows(db, OrderAttachment, order.id)) >= MAX_ATTACHMENTS:
        raise HTTPException(422, "An order can have at most five attachments")
    data = file.file.read(MAX_ATTACHMENT_BYTES + 1)
    media_type, scan_method = validate_attachment(file.filename, data, scanner)
    db.add(
        OrderAttachment(
            order_id=order.id,
            filename=file.filename,
            data=data,
            media_type=media_type,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            scan_method=scan_method,
        )
    )
    event(
        db,
        order,
        user.id,
        "attachment_added",
        "Supporting attachment added to the draft.",
    )
    db.commit()
    return read_order(db, order)


@router.delete(
    "/orders/{order_id}/attachments/{attachment_id}", response_model=OrderRead
)
def delete_attachment(
    order_id: Id,
    attachment_id: Id,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    draft_only(order)
    check_version(order.version, expected_version)
    attachment = db.scalar(
        select(OrderAttachment).where(
            OrderAttachment.id == attachment_id, OrderAttachment.order_id == order.id
        )
    )
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    db.delete(attachment)
    event(
        db,
        order,
        user.id,
        "attachment_removed",
        "Supporting attachment removed from the draft.",
    )
    db.commit()
    return read_order(db, order)


def download(db, order, attachment_id):
    attachment = db.scalar(
        select(OrderAttachment).where(
            OrderAttachment.order_id == order.id, OrderAttachment.id == attachment_id
        )
    )
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    return Response(
        attachment.data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + urlquote(attachment.filename, safe=""),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get("/orders/{order_id}/attachments/{attachment_id}/download")
def download_attachment(
    order_id: Id,
    attachment_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return download(db, get_order(db, user, order_id), attachment_id)


@router.get("/orders/{order_id}/timeline", response_model=list[EventRead])
def timeline(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    get_order(db, user, order_id)
    return rows(db, OrderEvent, order_id)


@router.get("/orders/{order_id}/messages", response_model=list[MessageRead])
def messages(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    get_order(db, user, order_id)
    return rows(db, OrderMessage, order_id)


def add_message(db, order, user, payload, *, staff=False):
    if order.submitted_at is None:
        raise HTTPException(409, "Submit the order before messaging the institution")
    if staff and user.id == order.user_id:
        raise HTTPException(403, "Another staff member must handle your order")
    if payload.in_reply_to_id:
        parent = db.scalar(
            select(OrderMessage).where(
                OrderMessage.id == payload.in_reply_to_id,
                OrderMessage.order_id == order.id,
            )
        )
        if parent is None:
            raise HTTPException(404, "Original message not found in this order")
        if not staff and parent.requires_response and parent.author_role == "staff":
            parent.answered_at = utcnow()
    requires_response = staff and payload.requires_response
    message = OrderMessage(
        order_id=order.id,
        actor_id=user.id,
        author_role="staff" if staff else "student",
        body=payload.body,
        requires_response=requires_response,
        in_reply_to_id=payload.in_reply_to_id,
    )
    db.add(message)
    event(
        db,
        order,
        user.id,
        "information_requested" if requires_response else "message_sent",
        "The institution requested additional information."
        if requires_response
        else "A message was added to the order.",
    )
    db.commit()
    db.refresh(message)
    return message


@router.post("/orders/{order_id}/messages", response_model=MessageRead, status_code=201)
def send_message(
    order_id: Id,
    payload: MessageInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return add_message(db, get_order(db, user, order_id, lock=True), user, payload)


@router.post("/orders/{order_id}/cancellation-requests", response_model=OrderRead)
def request_cancellation(
    order_id: Id,
    payload: CancellationInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    if order.status == "cancellation_requested":
        existing = db.scalar(
            select(OrderCancellation).where(
                OrderCancellation.order_id == order.id,
                OrderCancellation.status == "pending",
            )
        )
        if existing and existing.reason == payload.reason:
            return read_order(db, order)
    check_version(order.version, payload.expected_version)
    if order.status not in ("draft", "submitted") or not can_cancel(db, order):
        raise HTTPException(
            409,
            "This order is not eligible for cancellation at its current processing or payment stage",
        )
    cancellation = OrderCancellation(order_id=order.id, reason=payload.reason)
    if order.status == "draft":
        order.status = "cancelled"
        cancellation.status = "approved"
        cancellation.decision_reason = "Unsubmitted draft cancelled by its owner."
        cancellation.decided_by = user.id
        cancellation.decided_at = utcnow()
    else:
        order.status = "cancellation_requested"
    db.add(cancellation)
    event(
        db,
        order,
        user.id,
        "cancelled" if order.status == "cancelled" else "cancellation_requested",
        payload.reason,
    )
    db.commit()
    return read_order(db, order)


@router.get(
    "/staff/institutions/{institution_id}/orders", response_model=list[OrderRead]
)
def staff_orders(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    from app.core.permissions import require_academic_staff

    require_academic_staff(db, user, institution_id)
    return [
        read_order(db, order)
        for order in db.scalars(
            select(Order)
            .where(
                Order.institution_id == institution_id,
                Order.status != "draft",
                Order.submitted_at.is_not(None),
            )
            .order_by(Order.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get(
    "/staff/institutions/{institution_id}/orders/{order_id}", response_model=OrderRead
)
def staff_read_order(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return read_order(db, get_order(db, user, order_id, institution_id=institution_id))


@router.get(
    "/staff/institutions/{institution_id}/orders/{order_id}/timeline",
    response_model=list[EventRead],
)
def staff_timeline(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    get_order(db, user, order_id, institution_id=institution_id)
    return rows(db, OrderEvent, order_id)


@router.get(
    "/staff/institutions/{institution_id}/orders/{order_id}/messages",
    response_model=list[MessageRead],
)
def staff_messages(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    get_order(db, user, order_id, institution_id=institution_id)
    return rows(db, OrderMessage, order_id)


@router.post(
    "/staff/institutions/{institution_id}/orders/{order_id}/messages",
    response_model=MessageRead,
    status_code=201,
)
def staff_send_message(
    institution_id: Id,
    order_id: Id,
    payload: StaffMessageInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return add_message(
        db,
        get_order(db, user, order_id, institution_id=institution_id, lock=True),
        user,
        payload,
        staff=True,
    )


@router.get(
    "/staff/institutions/{institution_id}/orders/{order_id}/attachments/{attachment_id}/download"
)
def staff_download(
    institution_id: Id,
    order_id: Id,
    attachment_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    return download(
        db, get_order(db, user, order_id, institution_id=institution_id), attachment_id
    )


@router.post(
    "/staff/institutions/{institution_id}/orders/{order_id}/cancellation-decisions",
    response_model=OrderRead,
)
def decide_cancellation(
    institution_id: Id,
    order_id: Id,
    payload: CancellationDecision,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    from app.models.orders import OrderItem

    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    if user.id == order.user_id:
        raise HTTPException(403, "Another staff member must handle your cancellation")
    check_version(order.version, payload.expected_version)
    if order.status != "cancellation_requested":
        raise HTTPException(409, "There is no pending cancellation request")
    if payload.decision == "approved" and not can_cancel(db, order):
        raise HTTPException(
            409,
            "Processing or payment has progressed; cancellation needs further review",
        )
    cancellation = db.scalar(
        select(OrderCancellation).where(
            OrderCancellation.order_id == order.id,
            OrderCancellation.status == "pending",
        )
    )
    cancellation.status = payload.decision
    cancellation.decision_reason = payload.reason
    cancellation.decided_by = user.id
    cancellation.decided_at = utcnow()
    order.status = "cancelled" if payload.decision == "approved" else "submitted"
    if order.status == "cancelled":
        for item in rows(db, OrderItem, order.id):
            item.fulfillment_status = "cancelled"
    event(db, order, user.id, "cancellation_" + payload.decision, payload.reason)
    db.commit()
    return read_order(db, order)
