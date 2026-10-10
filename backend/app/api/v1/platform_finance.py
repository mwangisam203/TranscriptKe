from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.payments import overview
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.access import AccessEvent
from app.models.billing import InstitutionBilling
from app.models.institution import Institution
from app.models.orders import Order
from app.models.payments import PaymentAttempt, PaymentLedger, PaymentRefund
from app.models.user import User
from app.schemas.academic import Id
from app.schemas.common import StrictInput
from app.schemas.payments import RefundInput
from app.services.academic import check_version, lock_institution
from app.services.orders import utcnow
from app.services.payment_gateways import get_gateways
from app.services.payments import (
    MPESA_CONFIRMATION_SECONDS,
    aggregate_status,
    dispatch_refund,
    log,
    payment_for,
    public_payment,
    reconcile,
    request_refund,
)
from app.services.platform_finance import finance_order, require_finance_admin

router = APIRouter(prefix="/admin", tags=["platform finance"])


class BillingInput(StrictInput):
    enabled: bool
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


def billing_read(db, institution_id):
    value = db.get(InstitutionBilling, institution_id)
    return {
        "institution_id": institution_id,
        "enabled": bool(value and value.enabled),
        "version": value.version if value else 0,
    }


@router.get("/institutions/{institution_id}/billing")
def billing(
    institution_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_finance_admin(user)
    if not db.get(Institution, institution_id):
        raise HTTPException(404, "Institution not found")
    return billing_read(db, institution_id)


@router.put("/institutions/{institution_id}/billing")
def save_billing(
    institution_id: Id,
    payload: BillingInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_finance_admin(user)
    institution = lock_institution(db, institution_id)
    if payload.enabled and not institution.is_approved:
        raise HTTPException(409, "Approve the participating institution first")
    current = db.get(InstitutionBilling, institution_id)
    check_version(current.version if current else 0, payload.expected_version)
    if current is None:
        current = InstitutionBilling(
            institution_id=institution_id, enabled=payload.enabled, version=1
        )
        db.add(current)
    else:
        current.enabled = payload.enabled
        current.version += 1
    db.add(
        AccessEvent(
            actor_id=user.id,
            institution_id=institution_id,
            action="platform_collection_changed",
            details={"enabled": payload.enabled, "reason": payload.reason},
        )
    )
    db.commit()
    return billing_read(db, institution_id)


@router.get("/finance/payments")
def payments(
    institution_id: Id | None = None,
    attention_only: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_finance_admin(user)
    statement = (
        select(PaymentAttempt, Order, Institution)
        .join(Order, Order.id == PaymentAttempt.order_id)
        .join(Institution, Institution.id == Order.institution_id)
        .where(PaymentAttempt.merchant_scope == "platform")
    )
    if attention_only:
        statement = statement.where(
            or_(
                PaymentAttempt.status == "unknown",
                and_(
                    PaymentAttempt.provider == "mpesa",
                    PaymentAttempt.status.in_(("pending", "initiating")),
                    PaymentAttempt.created_at
                    <= utcnow() - timedelta(seconds=MPESA_CONFIRMATION_SECONDS),
                ),
                Order.payment_status.in_(
                    (
                        "review_required",
                        "disputed",
                        "refund_requested",
                        "refund_pending",
                    )
                ),
            )
        )
    if institution_id:
        statement = statement.where(Order.institution_id == institution_id)
    return [
        {
            **public_payment(db, payment),
            "order_id": order.id,
            "order_reference": order.reference,
            "order_version": order.version,
            "institution_id": institution.id,
            "institution_name": institution.name,
        }
        for payment, order, institution in db.execute(
            statement.order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id)
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/finance/collections")
def collections(
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    require_finance_admin(user)
    statement = (
        select(
            Order.institution_id,
            Institution.name,
            PaymentAttempt.mode,
            PaymentLedger.currency,
            func.sum(PaymentLedger.amount_minor).label("net_minor"),
        )
        .select_from(PaymentLedger)
        .join(PaymentAttempt, PaymentAttempt.id == PaymentLedger.payment_id)
        .join(Order, Order.id == PaymentAttempt.order_id)
        .join(Institution, Institution.id == Order.institution_id)
        .where(PaymentAttempt.merchant_scope == "platform")
        .group_by(
            Order.institution_id,
            Institution.name,
            PaymentAttempt.mode,
            PaymentLedger.currency,
        )
        .order_by(Order.institution_id, PaymentAttempt.mode)
        .offset(offset)
        .limit(limit)
    )
    return [dict(row._mapping) for row in db.execute(statement)]


def platform_payment(db, user, order_id, payment_id):
    order = finance_order(db, user, order_id, lock=True)
    payment = payment_for(db, order, payment_id)
    if payment.merchant_scope != "platform":
        raise HTTPException(
            409, "This payment uses the original institution merchant flow"
        )
    return order, payment


@router.get("/finance/orders/{order_id}/payments")
def payment_overview(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return overview(db, finance_order(db, user, order_id))


@router.post("/finance/orders/{order_id}/payments/{payment_id}/reconcile")
def check_payment(
    order_id: Id,
    payment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    order, payment = platform_payment(db, user, order_id, payment_id)
    return public_payment(db, reconcile(db, order, payment, gateway))


@router.post("/finance/orders/{order_id}/payments/{payment_id}/refunds")
def approve(
    order_id: Id,
    payment_id: UUID,
    payload: RefundInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    order, payment = platform_payment(db, user, order_id, payment_id)
    request_refund(
        db, order, payment, user, payload.reason, payload.expected_version, approve=True
    )
    return public_payment(
        db,
        dispatch_refund(
            db, user, None, order_id, str(payment_id), gateway, finance=True
        ),
    )


@router.post("/finance/orders/{order_id}/payments/{payment_id}/refund-rejections")
def reject(
    order_id: Id,
    payment_id: UUID,
    payload: RefundInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order, payment = platform_payment(db, user, order_id, payment_id)
    if order.user_id == user.id:
        raise HTTPException(403, "Another administrator must handle your refund")
    check_version(order.version, payload.expected_version)
    refund = db.scalar(
        select(PaymentRefund).where(PaymentRefund.payment_id == payment.id)
    )
    if not refund or refund.status != "requested":
        raise HTTPException(409, "There is no undecided refund request")
    refund.status = "rejected"
    order.payment_status = aggregate_status(db, order)
    log(db, order, payment, "refund_rejected", payload.reason, user.id)
    db.commit()
    return public_payment(db, payment)


class NoPaymentReview(StrictInput):
    expected_version: int = Field(ge=1)
    provider_case_reference: str = Field(min_length=5, max_length=150)
    evidence: str = Field(min_length=30, max_length=2000)
    confirmed_no_payment: Literal[True]


@router.post("/finance/orders/{order_id}/payments/{payment_id}/no-payment-review")
def resolve_unconfirmed_payment(
    order_id: Id,
    payment_id: UUID,
    payload: NoPaymentReview,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order, payment = platform_payment(db, user, order_id, payment_id)
    check_version(order.version, payload.expected_version)
    if order.user_id == user.id:
        raise HTTPException(
            403, "Another platform administrator must review your payment"
        )
    if (
        payment.status != "unknown"
        or payment.provider_reference
        or payment.paid_at
        or payment.refunded_minor
        or db.scalar(
            select(PaymentLedger.id)
            .where(PaymentLedger.payment_id == payment.id)
            .limit(1)
        )
        or db.scalar(
            select(PaymentRefund.id)
            .where(PaymentRefund.payment_id == payment.id)
            .limit(1)
        )
    ):
        raise HTTPException(
            409,
            "Only an unconfirmed payment without a provider reference can use this review",
        )
    if (
        len(payload.provider_case_reference.strip()) < 5
        or len(payload.evidence.strip()) < 30
    ):
        raise HTTPException(
            422, "Provide the provider case reference and investigation evidence"
        )
    payment.status = "failed"
    payment.active_order_id = None
    order.payment_status = aggregate_status(db, order)
    log(
        db,
        order,
        payment,
        "payment_rejected",
        "TranscriptsKE reviewed this unconfirmed request and confirmed no payment was received. You can retry checkout.",
        user.id,
    )
    db.add(
        AccessEvent(
            actor_id=user.id,
            institution_id=order.institution_id,
            subject_id=order.id,
            action="unconfirmed_payment_reviewed",
            details={
                "payment_id": payment.id,
                "provider_case_reference": payload.provider_case_reference.strip(),
                "evidence": payload.evidence.strip(),
                "outcome": "no_payment_received",
            },
        )
    )
    db.commit()
    return public_payment(db, payment)
