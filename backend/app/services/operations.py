"""Institution-scoped queries. Counts refer to orders, never sums of joined rows."""

from datetime import timedelta

from sqlalchemy import and_, case, func, or_, select

from app.core.config import settings
from app.models.access import InstitutionMembership
from app.models.fulfillment import OrderHold, RegistrarCase
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.operations import OperationsCase
from app.models.orders import Order, OrderItem, OrderMessage
from app.models.payments import PaymentAttempt, PaymentWebhook
from app.models.user import User
from app.services.order_timing import as_utc
from app.services.orders import utcnow


def exists_for(model, *conditions):
    return (
        select(model.order_id)
        .where(model.order_id == Order.id, *conditions)
        .correlate(Order)
        .exists()
    )


def flags(at):
    work_open = and_(
        Order.status == "submitted",
        exists_for(
            OrderItem,
            OrderItem.fulfillment_status.in_(
                ["awaiting_review", "processing", "ready"]
            ),
        ),
    )
    assigned = (
        select(RegistrarCase.order_id)
        .join(
            InstitutionMembership,
            and_(
                InstitutionMembership.user_id == RegistrarCase.assigned_to,
                InstitutionMembership.institution_id == Order.institution_id,
            ),
        )
        .join(User, User.id == RegistrarCase.assigned_to)
        .where(
            RegistrarCase.order_id == Order.id,
            InstitutionMembership.is_active.is_(True),
            User.is_email_verified.is_(True),
            RegistrarCase.assigned_to != Order.user_id,
        )
        .correlate(Order)
        .exists()
    )
    payment_cutoff = at - timedelta(minutes=settings.OPERATIONS_PAYMENT_PENDING_MINUTES)
    payment_wait = exists_for(
        PaymentAttempt,
        or_(
            PaymentAttempt.status == "unknown",
            and_(
                PaymentAttempt.status.in_(["initiating", "pending"]),
                PaymentAttempt.created_at <= payment_cutoff,
            ),
        ),
    )
    webhook_wait = (
        select(PaymentWebhook.id)
        .join(PaymentAttempt, PaymentAttempt.id == PaymentWebhook.payment_id)
        .where(
            PaymentAttempt.order_id == Order.id,
            PaymentWebhook.processed_at.is_(None),
            PaymentWebhook.created_at <= payment_cutoff,
        )
        .correlate(Order)
        .exists()
    )
    notification_cutoff = at - timedelta(
        minutes=settings.OPERATIONS_NOTIFICATION_PENDING_MINUTES
    )
    delivery_attention = (
        select(DocumentDelivery.id)
        .join(IssuedDocument, IssuedDocument.id == DocumentDelivery.document_id)
        .where(
            DocumentDelivery.order_id == Order.id,
            IssuedDocument.revoked_at.is_(None),
            or_(
                and_(
                    DocumentDelivery.notified_at.is_(None),
                    or_(
                        DocumentDelivery.notification_attempts > 0,
                        IssuedDocument.issued_at <= notification_cutoff,
                    ),
                ),
                and_(
                    DocumentDelivery.download_count == 0,
                    DocumentDelivery.expires_at <= at + timedelta(hours=24),
                ),
            ),
        )
        .correlate(Order)
        .exists()
    )
    follow_up = exists_for(
        OperationsCase,
        OperationsCase.follow_up_at.is_not(None),
        OperationsCase.follow_up_at <= at,
    )
    return {
        "open_fulfillment": work_open,
        "overdue": and_(work_open, Order.processing_due_at < at),
        "assignment_required": and_(work_open, ~assigned),
        "holds": and_(
            Order.status.in_(["submitted", "cancellation_requested"]),
            exists_for(OrderHold, OrderHold.resolved_at.is_(None)),
        ),
        "awaiting_student": and_(
            Order.status.in_(["submitted", "cancellation_requested"]),
            exists_for(
                OrderMessage,
                OrderMessage.requires_response.is_(True),
                OrderMessage.answered_at.is_(None),
            ),
        ),
        "payments": or_(
            Order.payment_status.in_(
                [
                    "refund_requested",
                    "refund_pending",
                    "disputed",
                    "partially_refunded",
                    "review_required",
                ]
            ),
            payment_wait,
            webhook_wait,
        ),
        "deliveries": delivery_attention,
        "cancellations": Order.status == "cancellation_requested",
        "ready": and_(
            Order.status == "submitted",
            exists_for(OrderItem, OrderItem.fulfillment_status == "ready"),
        ),
        "follow_up": follow_up,
        "missing_target": and_(work_open, Order.processing_due_at.is_(None)),
    }


def scope(institution_id):
    return (Order.institution_id == institution_id, Order.submitted_at.is_not(None))


def summary(db, institution_id):
    at = utcnow()
    conditions = flags(at)
    result = (
        db.execute(
            select(
                func.count(Order.id).label("submitted_total"),
                *[
                    func.coalesce(func.sum(case((condition, 1), else_=0)), 0).label(
                        name
                    )
                    for name, condition in conditions.items()
                ],
            ).where(*scope(institution_id))
        )
        .mappings()
        .one()
    )
    return {
        "as_of": at,
        "counts": dict(result),
        "timing_policy": "Targets use the longest quoted processing time, Monday–Friday after submission, ending at 23:59:59 Africa/Nairobi. Holidays, holds and deferred release do not pause this planning clock; this is not a guaranteed delivery date.",
    }


def queue(db, institution_id, kind, offset, limit):
    at = utcnow()
    conditions = flags(at)
    filters = list(scope(institution_id))
    if kind != "all":
        filters.append(conditions[kind])
    total = db.scalar(select(func.count(Order.id)).where(*filters))
    statement = (
        select(
            Order.id,
            Order.reference,
            Order.version,
            Order.status,
            Order.payment_status,
            Order.submitted_at,
            Order.processing_due_at,
            Order.release_when,
            *[condition.label(name) for name, condition in conditions.items()],
        )
        .where(*filters)
        .order_by(
            Order.processing_due_at.asc().nullslast(), Order.submitted_at, Order.id
        )
        .offset(offset)
        .limit(limit)
    )
    entries = []
    for row in db.execute(statement).mappings():
        data = dict(row)
        data["processing_due_at"] = as_utc(data["processing_due_at"])
        data["submitted_at"] = as_utc(data["submitted_at"])
        data["flags"] = [name for name in conditions if data.pop(name)]
        entries.append(data)
    return {
        "as_of": at,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": entries,
    }
