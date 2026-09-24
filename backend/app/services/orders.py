import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.permissions import require_academic_staff
from app.models.academic import AcademicRecordLink, InstitutionService, OrderingPolicy
from app.models.institution import Institution
from app.models.orders import (
    Order,
    OrderAttachment,
    OrderCancellation,
    OrderConsent,
    OrderEvent,
    OrderItem,
    OrderMessage,
    OrderQuote,
    OrderRecipient,
)
from app.models.user import User
from app.schemas.orders import AttachmentRead, CancellationRead, DraftInput, OrderRead
from app.services.academic import check_version
from app.services.order_timing import planning_target

CONSENT_VERSION = "2026-09-v1"
CONSENT_TEXT = (
    "I authorize the institution shown in this order to release the selected academic "
    "documents and supporting attachments to the named recipients for the stated purpose, "
    "using the specified delivery methods and release instructions. I have reviewed the "
    "recipient details and quoted fees. This authorization applies only to this order."
)


def utcnow():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def rows(db, model, order_id):
    return db.scalars(
        select(model).where(model.order_id == order_id).order_by(model.id)
    ).all()


def get_order(
    db: Session, user: User, order_id: int, *, institution_id=None, lock=False
):
    statement = select(Order).where(Order.id == order_id)
    if institution_id is None:
        statement = statement.where(Order.user_id == user.id)
    else:
        require_academic_staff(db, user, institution_id)
        statement = statement.where(
            Order.institution_id == institution_id,
            Order.status != "draft",
            Order.submitted_at.is_not(None),
        )
    order = db.scalar(statement)
    if order is None:
        raise HTTPException(404, "Order not found")
    if lock:
        # Same ordering as catalog and record matching writes; cancellations remain possible after suspension.
        db.scalar(
            select(Institution)
            .where(Institution.id == order.institution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        order = db.scalar(
            statement.with_for_update().execution_options(populate_existing=True)
        )
        if institution_id is not None:
            require_academic_staff(db, user, institution_id)
    return order


def draft_only(order):
    if order.status != "draft":
        raise HTTPException(
            409,
            "Submitted orders cannot be edited. Request cancellation or create a new draft.",
        )


def event(db, order, actor_id, kind, message, *, bump=True):
    if bump:
        order.version += 1
    order.updated_at = utcnow()
    db.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            actor_id=actor_id,
            kind=kind,
            message=message,
        )
    )


def recipient_data(recipient):
    return {
        name: getattr(recipient, name)
        for name in (
            "key",
            "name",
            "organization",
            "email",
            "delivery_method",
            "postal_address",
            "application_reference",
        )
    }


def item_data(item):
    return {
        name: getattr(item, name)
        for name in ("key", "service_id", "recipient_key", "quantity")
    }


def draft_data(db, order):
    return {
        "expected_version": order.version,
        "purpose": order.purpose,
        "release_when": order.release_when,
        "release_instruction": order.release_instruction,
        "recipients": [
            recipient_data(row) for row in rows(db, OrderRecipient, order.id)
        ],
        "items": [item_data(row) for row in rows(db, OrderItem, order.id)],
    }


def read_order(db, order):
    data = draft_data(db, order)
    data.pop("expected_version")
    data["items"] = [
        {**item_data(row), "fulfillment_status": row.fulfillment_status}
        for row in rows(db, OrderItem, order.id)
    ]
    return OrderRead(
        id=order.id,
        reference=order.reference,
        institution_id=order.institution_id,
        academic_record_link_id=order.academic_record_link_id,
        status=order.status,
        payment_status=order.payment_status,
        version=order.version,
        **data,
        attachments=[
            AttachmentRead.model_validate(row)
            for row in rows(db, OrderAttachment, order.id)
        ],
        cancellations=[
            CancellationRead.model_validate(row)
            for row in rows(db, OrderCancellation, order.id)
        ],
        unanswered_questions=db.scalar(
            select(func.count())
            .select_from(OrderMessage)
            .where(
                OrderMessage.order_id == order.id,
                OrderMessage.requires_response.is_(True),
                OrderMessage.answered_at.is_(None),
            )
        ),
        submitted_snapshot=order.submitted_snapshot,
        created_at=order.created_at,
        updated_at=order.updated_at,
        submitted_at=order.submitted_at,
    )


def replace_draft(db, order, user, payload: DraftInput):
    draft_only(order)
    check_version(order.version, payload.expected_version)
    # Validate service ownership even in an incomplete draft. Availability is rechecked at quote and submission.
    for item in payload.items:
        service = db.get(InstitutionService, item.service_id)
        if service is None or service.institution_id != order.institution_id:
            raise HTTPException(
                422, "Every document service must belong to this order's institution"
            )
    db.execute(delete(OrderItem).where(OrderItem.order_id == order.id))
    db.execute(delete(OrderRecipient).where(OrderRecipient.order_id == order.id))
    order.purpose = payload.purpose.strip()
    order.release_when = payload.release_when
    order.release_instruction = payload.release_instruction.strip()
    for recipient in payload.recipients:
        db.add(OrderRecipient(order_id=order.id, **recipient.model_dump(mode="json")))
    db.flush()
    for item in payload.items:
        db.add(OrderItem(order_id=order.id, **item.model_dump(mode="json")))
    event(
        db,
        order,
        user.id,
        "draft_updated",
        "Draft details updated; review a fresh quote before submitting.",
    )
    db.flush()


def build_snapshot(db, order):
    institution = db.get(Institution, order.institution_id)
    policy = db.get(OrderingPolicy, order.institution_id)
    link = db.get(AcademicRecordLink, order.academic_record_link_id)
    problems = []
    if not institution or not institution.is_active or not institution.is_approved:
        problems.append("The institution must be active and approved.")
    if policy is None or not policy.accepting_requests:
        problems.append("The institution is not accepting new orders.")
    if (
        link is None
        or link.user_id != order.user_id
        or link.institution_id != order.institution_id
        or link.status != "matched"
    ):
        problems.append("A confirmed academic record belonging to you is required.")
    if not order.purpose:
        problems.append("Provide a purpose for releasing these documents.")
    if order.release_when != "now" and not order.release_instruction:
        problems.append("Describe the grades or graduation event to wait for.")
    recipients = rows(db, OrderRecipient, order.id)
    items = rows(db, OrderItem, order.id)
    if not recipients or not items:
        problems.append("Add at least one recipient and document item.")
    used_keys = {item.recipient_key for item in items}
    if any(recipient.key not in used_keys for recipient in recipients):
        problems.append("Each recipient must have at least one requested document.")
    recipient_map = {r.key: r for r in recipients}
    priced_items = []
    for item in items:
        service = db.get(InstitutionService, item.service_id)
        if (
            service is None
            or service.institution_id != order.institution_id
            or not service.is_active
        ):
            problems.append(f"Document item {item.key} is no longer available.")
            continue
        recipient = recipient_map[item.recipient_key]
        if recipient.delivery_method not in service.delivery_methods:
            problems.append(
                f"The delivery method for item {item.key} is not offered by that service."
            )
        required = set(service.required_fields) | set(
            policy.required_fields if policy else []
        )
        if link and any(getattr(link, field) is None for field in required):
            problems.append(
                "The confirmed academic record is missing information now required by the institution."
            )
        priced_items.append(
            {
                **item_data(item),
                "name": service.name,
                "document_type": service.document_type,
                "service_version": service.version,
                "unit_fee_minor": service.fee_minor,
                "line_total_minor": service.fee_minor * item.quantity,
                "processing_days_min": service.processing_days_min,
                "processing_days_max": service.processing_days_max,
            }
        )
    if problems:
        raise HTTPException(
            422,
            {
                "message": "Order is not ready to submit",
                "fields": list(dict.fromkeys(problems)),
            },
        )
    return {
        "institution": {
            "id": institution.id,
            "name": institution.name,
            "code": institution.code,
        },
        "academic_record": {
            "id": link.id,
            "version": link.version,
            "name_on_record": link.name_on_record,
            "admission_number": link.admission_number,
            "program": link.program,
        },
        "policy_version": policy.version,
        "purpose": order.purpose,
        "release_when": order.release_when,
        "release_instruction": order.release_instruction,
        "recipients": [recipient_data(r) for r in recipients],
        "items": priced_items,
        "attachments": [
            AttachmentRead.model_validate(row).model_dump(mode="json")
            for row in rows(db, OrderAttachment, order.id)
        ],
        "currency": "KES",
        "total_minor": sum(item["line_total_minor"] for item in priced_items),
        "consent_version": CONSENT_VERSION,
    }


def make_quote(db, order):
    draft_only(order)
    snapshot = build_snapshot(db, order)
    quote = OrderQuote(
        order_id=order.id,
        order_version=order.version,
        scope_hash=digest(snapshot),
        snapshot=snapshot,
        total_minor=snapshot["total_minor"],
        expires_at=utcnow() + timedelta(minutes=15),
    )
    db.add(quote)
    db.flush()
    return quote


def valid_quote(db, order, quote_id):
    quote = db.scalar(
        select(OrderQuote).where(
            OrderQuote.id == str(quote_id), OrderQuote.order_id == order.id
        )
    )
    if quote is None:
        raise HTTPException(404, "Quote not found")
    expires = (
        quote.expires_at.replace(tzinfo=timezone.utc)
        if quote.expires_at.tzinfo is None
        else quote.expires_at
    )
    if expires <= utcnow() or quote.order_version != order.version:
        raise HTTPException(
            409,
            "Quote expired or draft changed. Request a fresh quote and consent again.",
        )
    if digest(build_snapshot(db, order)) != quote.scope_hash:
        raise HTTPException(
            409,
            "Institution settings or academic record changed. Review a fresh quote and consent again.",
        )
    return quote


def submit_order(db, order, user, payload, key):
    if order.status != "draft":
        if (
            order.submission_key == key
            and order.submission_quote_id == str(payload.quote_id)
            and order.submission_consent_id == payload.consent_id
        ):
            return  # A retry never creates a second submission/event, even after later cancellation.
        raise HTTPException(409, "This order has already been submitted or cancelled")
    check_version(order.version, payload.expected_version)
    quote = valid_quote(db, order, payload.quote_id)
    consent = db.scalar(
        select(OrderConsent).where(
            OrderConsent.id == payload.consent_id,
            OrderConsent.order_id == order.id,
            OrderConsent.user_id == user.id,
            OrderConsent.quote_id == quote.id,
        )
    )
    if (
        consent is None
        or consent.scope_hash != quote.scope_hash
        or consent.text_version != CONSENT_VERSION
    ):
        raise HTTPException(
            422, "Consent to this exact quote and recipient scope is required"
        )
    order.status = "submitted"
    order.submitted_snapshot = quote.snapshot
    order.submitted_at = utcnow()
    order.processing_due_at = planning_target(order.submitted_at, quote.snapshot)
    order.submission_key = key
    order.submission_quote_id = quote.id
    order.submission_consent_id = consent.id
    for item in rows(db, OrderItem, order.id):
        item.fulfillment_status = "awaiting_review"
    event(
        db,
        order,
        user.id,
        "submitted",
        "Order submitted to the institution. Payment has not been collected.",
    )


def can_cancel(db, order):
    # Later fulfillment/payment milestones must extend this gate deliberately.
    return order.payment_status == "not_started" and all(
        item.fulfillment_status in ("draft", "awaiting_review", "rejected")
        for item in rows(db, OrderItem, order.id)
    )
