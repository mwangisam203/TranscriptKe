import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models.institution import Institution
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.orders import Order, OrderItem
from app.models.payments import PaymentAttempt
from app.services.orders import event, rows, utcnow


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def documents(db, order):
    return list(
        db.scalars(
            select(IssuedDocument)
            .where(IssuedDocument.order_id == order.id)
            .order_by(IssuedDocument.created_at, IssuedDocument.id)
        )
    )


def payment_blockers(db, order, mode):
    total = (order.submitted_snapshot or {}).get("total_minor")
    if total == 0:
        return []
    payments = list(
        db.scalars(
            select(PaymentAttempt).where(
                PaymentAttempt.order_id == order.id,
                PaymentAttempt.status == "succeeded",
                PaymentAttempt.paid_at.is_not(None),
                PaymentAttempt.refunded_minor == 0,
            )
        )
    )
    if (
        order.payment_status != "paid"
        or len(payments) != 1
        or payments[0].amount_minor != total
    ):
        return [
            "Verified payment is required before release; a matching provider-confirmed payment is missing."
        ]
    required = "live" if mode == "live" else "test"
    if payments[0].mode != required:
        return [
            f"{mode.capitalize()} issuance requires {required} payment; test payments cannot authorize production issuance."
        ]
    return []


def release_blockers(db, order):
    from app.services.fulfillment import preparation_blockers

    blockers = preparation_blockers(db, order)
    if not settings.ISSUANCE_ENABLED:
        blockers.append("Secure issuance and delivery are disabled in configuration.")
    blockers.extend(payment_blockers(db, order, settings.ISSUANCE_MODE))
    items = rows(db, OrderItem, order.id)
    if not items or any(
        i.fulfillment_status not in ("ready", "issued", "delivered") for i in items
    ):
        blockers.append("Every document must be ready before release.")
    pending = [i for i in items if i.fulfillment_status == "ready"]
    prepared = {
        d.item_id
        for d in documents(db, order)
        if not d.revoked_at and not d.issued_at and d.mode == settings.ISSUANCE_MODE
    }
    if any(i.id not in prepared for i in pending):
        blockers.append("Upload a scanned PDF for every ready document before release.")
    if not pending:
        blockers.append("No documents are awaiting release.")
    return blockers


def document_for(db, order, document_id):
    doc = db.scalar(
        select(IssuedDocument)
        .where(
            IssuedDocument.id == str(document_id), IssuedDocument.order_id == order.id
        )
        .execution_options(populate_existing=True)
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


def public_document(db, doc, *, staff=False):
    delivery = db.scalar(
        select(DocumentDelivery).where(DocumentDelivery.document_id == doc.id)
    )
    result = {
        "id": doc.id,
        "item_id": doc.item_id,
        "item_key": db.get(OrderItem, doc.item_id).key,
        "mode": doc.mode,
        "status": "revoked"
        if doc.revoked_at
        else "issued"
        if doc.issued_at
        else "prepared",
        "issued_at": doc.issued_at,
        "revoked_at": doc.revoked_at,
        "revocation_reason": doc.revocation_reason,
        "delivery": {
            "expires_at": delivery.expires_at,
            "notified_at": delivery.notified_at,
            "download_count": delivery.download_count,
            "last_downloaded_at": delivery.last_downloaded_at,
        }
        if delivery
        else None,
    }
    if staff:
        result.update(
            filename=doc.filename,
            sha256=doc.sha256,
            size=doc.size,
            created_at=doc.created_at,
            uploaded_by=doc.uploaded_by,
            issued_by=doc.issued_by,
            notification_attempts=delivery.notification_attempts if delivery else 0,
        )
    return result


def locked_delivery(db, identifier):
    delivery = db.get(DocumentDelivery, str(identifier))
    if not delivery:
        raise HTTPException(404, "Delivery unavailable")
    order = db.get(Order, delivery.order_id)
    db.scalar(
        select(Institution)
        .where(Institution.id == order.institution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    db.refresh(delivery)
    doc = document_for(db, order, delivery.document_id)
    return order, doc, delivery


def require_delivery(db, order, doc, delivery):
    from app.services.fulfillment import preparation_blockers

    if (
        not settings.ISSUANCE_ENABLED
        or doc.mode != settings.ISSUANCE_MODE
        or not doc.issued_at
        or doc.revoked_at
        or aware(delivery.expires_at) <= utcnow()
        or preparation_blockers(db, order)
        or payment_blockers(db, order, doc.mode)
    ):
        raise HTTPException(
            410, "Delivery unavailable; contact the issuing institution"
        )


def notify_delivery(db, order, doc, delivery, mailer):
    delivery.notification_attempts += 1
    delivery.notification_attempted_at = utcnow()
    try:
        require_delivery(db, order, doc, delivery)
        mailer.send_document(delivery.recipient_email, delivery.id, doc.mode)
    except HTTPException:
        db.commit()
        raise
    delivery.notified_at = utcnow()
    event(
        db,
        order,
        None,
        "document_notification_sent",
        "A secure document availability email was accepted for delivery.",
    )
    db.commit()


def access_code(db, order, doc, delivery, email, mailer):
    require_delivery(db, order, doc, delivery)
    if not secrets.compare_digest(
        email.casefold().encode(), delivery.recipient_email.casefold().encode()
    ):
        return
    code = secrets.token_urlsafe(32)
    delivery.code_hash = hashlib.sha256(code.encode()).hexdigest()
    delivery.code_expires_at = utcnow() + timedelta(minutes=15)
    mailer.send_token(delivery.recipient_email, "document_access", code, 15)
    db.commit()


def download(db, order, doc, delivery, code):
    require_delivery(db, order, doc, delivery)
    if (
        not delivery.code_hash
        or not delivery.code_expires_at
        or aware(delivery.code_expires_at) <= utcnow()
        or not secrets.compare_digest(
            delivery.code_hash, hashlib.sha256(code.encode()).hexdigest()
        )
    ):
        raise HTTPException(403, "Invalid or expired access code")
    data = doc.data
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        raise HTTPException(
            409, "Document integrity check failed; contact the institution"
        )
    delivery.code_hash = None
    delivery.code_expires_at = None
    delivery.download_count += 1
    delivery.last_downloaded_at = utcnow()
    db.get(OrderItem, doc.item_id).fulfillment_status = "delivered"
    event(
        db,
        order,
        None,
        "document_downloaded",
        "The verified recipient requested the document download.",
    )
    db.commit()
    return data
