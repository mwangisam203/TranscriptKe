import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.v1.orders import IdempotencyKey
from app.core.config import settings
from app.core.permissions import require_academic_staff
from app.core.security import get_verified_user
from app.db.session import get_db
from app.models.orders import Order
from app.models.payments import (
    PaymentAttempt,
    PaymentEvent,
    PaymentLedger,
    PaymentRefund,
    PaymentWebhook,
)
from app.models.user import User
from app.schemas.academic import Id
from app.schemas.payments import PaymentCreate, RefundInput
from app.services.academic import check_version
from app.services.orders import digest, get_order, utcnow
from app.services.payment_gateways import (
    configured,
    get_gateways,
    minor_amount,
    verify_callback,
    verify_stripe,
)
from app.services.payments import (
    aggregate_status,
    consume_reversal,
    create_attempt,
    dispatch,
    dispatch_refund,
    eligibility,
    log,
    payment_for,
    provider_available,
    public_payment,
    reconcile,
    request_refund,
)
from app.services.throttle import enforce_rate_limit

router = APIRouter(tags=["payments"])
OWN = "/orders/{order_id}/payments"
STAFF = "/staff/institutions/{institution_id}/orders/{order_id}/payments"


def overview(db, order):
    attempts = db.scalars(
        select(PaymentAttempt)
        .where(PaymentAttempt.order_id == order.id)
        .order_by(PaymentAttempt.created_at)
    ).all()
    methods = [
        provider
        for provider in ("stripe", "mpesa")
        if order.institution_id == settings.PAYMENT_INSTITUTION_ID
        and configured(provider)
    ]
    if (order.submitted_snapshot or {}).get("total_minor", 0) % 100:
        methods = [method for method in methods if method != "mpesa"]
    return {
        "order_id": order.id,
        "version": order.version,
        "payment_status": order.payment_status,
        "available_methods": methods,
        "mode": settings.PAYMENT_MODE,
        "blockers": eligibility(db, order),
        "attempts": [public_payment(db, p) for p in attempts],
    }


@router.get(OWN)
def own_payments(
    order_id: Id, db: Session = Depends(get_db), user: User = Depends(get_verified_user)
):
    return overview(db, get_order(db, user, order_id))


@router.post(OWN, status_code=201)
def start_payment(
    request: Request,
    order_id: Id,
    payload: PaymentCreate,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    enforce_rate_limit(
        db, request, "payment_start", str(user.id), account_limit=10, ip_limit=60
    )
    order = get_order(db, user, order_id, lock=True)
    payment = create_attempt(db, user, order, payload, idempotency_key)
    payment = dispatch(db, user, order_id, payment.id, gateway)
    return public_payment(db, payment)


@router.post(OWN + "/{payment_id}/retry")
def retry_payment(
    order_id: Id,
    payment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    return public_payment(
        db, dispatch(db, user, order_id, str(payment_id), gateway, retry=True)
    )


@router.post(OWN + "/{payment_id}/reconcile")
def check_payment(
    request: Request,
    order_id: Id,
    payment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    enforce_rate_limit(
        db, request, "payment_check", str(user.id), account_limit=60, ip_limit=180
    )
    order = get_order(db, user, order_id, lock=True)
    payment = payment_for(db, order, payment_id)
    return public_payment(db, reconcile(db, order, payment, gateway))


@router.get(OWN + "/{payment_id}/receipt")
def receipt(
    order_id: Id,
    payment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id)
    payment = payment_for(db, order, payment_id)
    if not payment.paid_at:
        raise HTTPException(
            409, "A receipt is available only after provider confirmation"
        )
    return {
        "receipt_reference": "PAY-" + payment.id,
        "order_reference": order.reference,
        "provider": payment.provider,
        "mode": payment.mode,
        "status": payment.status,
        "amount_minor": payment.amount_minor,
        "refunded_minor": payment.refunded_minor,
        "currency": payment.currency,
        "paid_at": payment.paid_at,
        "provider_reference": payment.transaction_reference
        or payment.provider_reference,
        "notice": "Payment acknowledgment, not a tax invoice. Test payments do not represent money received.",
    }


@router.post(OWN + "/{payment_id}/refund-requests")
def student_refund(
    order_id: Id,
    payment_id: UUID,
    payload: RefundInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, lock=True)
    payment = payment_for(db, order, payment_id)
    request_refund(db, order, payment, user, payload.reason, payload.expected_version)
    return public_payment(db, payment)


@router.get(STAFF)
def staff_payments(
    institution_id: Id,
    order_id: Id,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, institution_id=institution_id)
    result = overview(db, order)
    result["ledger"] = [
        dict(
            payment_id=e.payment_id,
            amount_minor=e.amount_minor,
            currency=e.currency,
            entry_key=e.entry_key,
            created_at=e.created_at,
        )
        for e in db.scalars(
            select(PaymentLedger)
            .join(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentLedger.id)
        )
    ]
    result["events"] = [
        dict(
            payment_id=e.payment_id,
            kind=e.kind,
            message=e.message,
            created_at=e.created_at,
        )
        for e in db.scalars(
            select(PaymentEvent)
            .join(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentEvent.id)
        )
    ]
    return result


@router.post(STAFF + "/{payment_id}/reconcile")
def staff_reconcile(
    institution_id: Id,
    order_id: Id,
    payment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    payment = payment_for(db, order, payment_id)
    return public_payment(db, reconcile(db, order, payment, gateway))


@router.post(STAFF + "/{payment_id}/refunds")
def approve_refund(
    institution_id: Id,
    order_id: Id,
    payment_id: UUID,
    payload: RefundInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
    gateway=Depends(get_gateways),
):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    require_academic_staff(db, user, institution_id, manage=True)
    payment = payment_for(db, order, payment_id)
    request_refund(
        db, order, payment, user, payload.reason, payload.expected_version, approve=True
    )
    return public_payment(
        db,
        dispatch_refund(db, user, institution_id, order_id, str(payment_id), gateway),
    )


@router.post(STAFF + "/{payment_id}/refund-rejections")
def reject_refund(
    institution_id: Id,
    order_id: Id,
    payment_id: UUID,
    payload: RefundInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    require_academic_staff(db, user, institution_id, manage=True)
    if order.user_id == user.id:
        raise HTTPException(403, "Another manager must handle your refund")
    check_version(order.version, payload.expected_version)
    payment = payment_for(db, order, payment_id)
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


@router.get("/staff/institutions/{institution_id}/payment-reconciliation")
def reconciliation_queue(
    institution_id: Id,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_verified_user),
):
    require_academic_staff(db, user, institution_id)
    payments = db.scalars(
        select(PaymentAttempt)
        .join(Order, PaymentAttempt.order_id == Order.id)
        .where(
            Order.institution_id == institution_id,
            Order.payment_status.in_(
                [
                    "pending",
                    "refund_requested",
                    "refund_pending",
                    "disputed",
                    "partially_refunded",
                    "review_required",
                ]
            ),
        )
        .order_by(PaymentAttempt.created_at)
        .offset(offset)
        .limit(limit)
    )
    return [{"order_id": p.order_id, **public_payment(db, p)} for p in payments]


async def webhook_body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, "Webhook exceeds size limit")
    return bytes(data)


def locked_system_payment(db, payment_id):
    # The same institution -> order lock order used by registrar and student mutations.
    from app.models.institution import Institution

    payment = db.get(PaymentAttempt, str(payment_id))
    if payment is None:
        raise HTTPException(404, "Payment not found")
    order = db.get(Order, payment.order_id)
    db.scalar(
        select(Institution)
        .where(Institution.id == order.institution_id)
        .with_for_update()
    )
    order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    payment = payment_for(db, order, payment_id)
    provider_available(payment)
    return order, payment


def inbox(db, payment, key, payload):
    record = db.scalar(
        select(PaymentWebhook).where(
            PaymentWebhook.provider == payment.provider, PaymentWebhook.event_key == key
        )
    )
    if record is None:
        record = PaymentWebhook(
            provider=payment.provider,
            event_key=key,
            payment_id=payment.id,
            payload=payload,
        )
        db.add(record)
        db.commit()
    elif record.payment_id != payment.id:
        raise HTTPException(409, "Webhook correlation mismatch")
    return record


def process_stripe(db, payload, gateway):
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(400, "Invalid Stripe event data")
    obj = data.get("object", {})
    if not isinstance(obj, dict):
        raise HTTPException(400, "Invalid Stripe event object")
    payment = None
    if str(payload.get("type", "")).startswith("checkout.session."):
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            raise HTTPException(400, "Invalid Stripe metadata")
        identifier = metadata.get("payment_id")
        if identifier:
            payment = db.get(PaymentAttempt, str(identifier))
    elif obj.get("payment_intent"):
        ref = obj["payment_intent"]
        if isinstance(ref, str):
            payment = db.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.provider == "stripe",
                    PaymentAttempt.transaction_reference == ref,
                )
            )
    if payment is None or payment.provider != "stripe":
        return {"received": True, "ignored": True}
    order, payment = locked_system_payment(db, payment.id)
    if str(payload.get("type", "")).startswith("checkout.session."):
        ref = obj.get("id")
        if not isinstance(ref, str) or not ref.startswith("cs_") or len(ref) > 150:
            raise HTTPException(400, "Invalid checkout reference")
        if payment.provider_reference and payment.provider_reference != ref:
            raise HTTPException(400, "Checkout reference mismatch")
        payment.provider_reference = (
            ref  # Signed event can recover a response lost after provider creation.
        )
    record = inbox(
        db,
        payment,
        payload["id"],
        {"type": payload.get("type"), "object_id": obj.get("id")},
    )
    if record.processed_at:
        return {"received": True}
    order, payment = locked_system_payment(db, payment.id)
    reconcile(db, order, payment, gateway)
    record.processed_at = utcnow()
    db.commit()
    return {"received": True}


@router.post("/payments/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default=""),
    db: Session = Depends(get_db),
    gateway=Depends(get_gateways),
):
    payload = verify_stripe(await webhook_body(request), stripe_signature)
    return await run_in_threadpool(process_stripe, db, payload, gateway)


def process_mpesa(db, payment_id, payload, gateway):
    order, payment = locked_system_payment(db, payment_id)
    if payment.provider != "mpesa":
        raise HTTPException(400, "Wrong payment provider")
    try:
        callback = payload["Body"]["stkCallback"]
        ref = callback["CheckoutRequestID"]
        if not isinstance(ref, str) or not 1 <= len(ref) <= 150:
            raise ValueError
        if payment.provider_reference and payment.provider_reference != ref:
            raise ValueError
        receipt = None
        if str(callback["ResultCode"]) == "0":
            metadata = {
                item["Name"]: item.get("Value")
                for item in callback["CallbackMetadata"]["Item"]
            }
            if (
                minor_amount(metadata["Amount"]) != payment.amount_minor
                or str(metadata["PhoneNumber"]) != payment.phone
            ):
                raise ValueError
            receipt = metadata["MpesaReceiptNumber"]
            if (
                not isinstance(receipt, str)
                or not receipt.isalnum()
                or not 1 <= len(receipt) <= 30
            ):
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, "M-Pesa callback does not match this payment") from exc
    if (
        receipt
        and db.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.provider == "mpesa",
                PaymentAttempt.transaction_reference == receipt,
                PaymentAttempt.id != payment.id,
            )
        )
        is not None
    ):
        raise HTTPException(409, "Provider receipt requires investigation")
    payment.provider_reference = ref
    record = inbox(
        db,
        payment,
        digest({"payment_id": payment.id, "payload": payload}),
        {"reference": ref, "receipt": receipt},
    )
    if record.processed_at:
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    order, payment = locked_system_payment(db, payment.id)
    # A callback alone cannot mark the order paid; Daraja must confirm the known STK request.
    reconcile(db, order, payment, gateway)
    if receipt and payment.status in ("succeeded", "refunded", "partially_refunded"):
        if payment.transaction_reference and payment.transaction_reference != receipt:
            raise HTTPException(409, "M-Pesa receipt mismatch")
        payment.transaction_reference = receipt
    # Pending query results remain replayable when callback delivery races provider status.
    if payment.status not in ("pending", "unknown", "initiating"):
        record.processed_at = utcnow()
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@router.post("/payments/webhooks/mpesa/payments/{payment_id}")
async def mpesa_webhook(
    payment_id: UUID,
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    gateway=Depends(get_gateways),
):
    verify_callback("payments", str(payment_id), token)
    try:
        payload = json.loads(await webhook_body(request))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    return await run_in_threadpool(process_mpesa, db, str(payment_id), payload, gateway)


def process_reversal(db, refund_id, payload, timeout=False):
    refund = db.get(PaymentRefund, refund_id)
    if refund is None:
        raise HTTPException(404, "Refund not found")
    order, payment = locked_system_payment(db, refund.payment_id)
    db.refresh(refund)
    if payment.provider != "mpesa":
        raise HTTPException(400, "Wrong refund provider")
    result = payload.get("Result", {}) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        raise HTTPException(400, "Invalid reversal result")
    if not result.get("ConversationID") or not result.get("OriginatorConversationID"):
        raise HTTPException(400, "Missing reversal correlation")
    record = inbox(
        db,
        payment,
        digest({"refund_id": refund_id, "timeout": timeout, "payload": payload}),
        {
            "refund_id": refund_id,
            "timeout": timeout,
            "result": {
                key: result.get(key)
                for key in ("ConversationID", "OriginatorConversationID", "ResultCode")
            },
        },
    )
    order, payment = locked_system_payment(db, payment.id)
    db.refresh(refund)
    consume_reversal(db, order, payment, refund, record)
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@router.post("/payments/webhooks/mpesa/reversals/{refund_id}")
@router.post("/payments/webhooks/mpesa/reversal-timeouts/{refund_id}")
async def mpesa_reversal(
    refund_id: UUID,
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    timeout = "/reversal-timeouts/" in request.url.path
    verify_callback(
        "reversal-timeouts" if timeout else "reversals", str(refund_id), token
    )
    try:
        payload = json.loads(await webhook_body(request))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    return await run_in_threadpool(
        process_reversal, db, str(refund_id), payload, timeout
    )
