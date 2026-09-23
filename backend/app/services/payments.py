from datetime import timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models.orders import OrderItem
from app.models.payments import (
    PaymentAttempt,
    PaymentEvent,
    PaymentLedger,
    PaymentRefund,
    PaymentWebhook,
)
from app.services.academic import check_version
from app.services.fulfillment import preparation_blockers
from app.services.orders import digest, event, get_order, rows, utcnow
from app.services.payment_gateways import (
    GatewayRejected,
    GatewayUnavailable,
    callback_url,
    configured,
    fingerprint,
)

OPEN = {"initiating", "pending", "unknown"}
SETTLED = {"succeeded", "partially_refunded", "refunded", "disputed"}


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def payment_for(db, order, payment_id):
    payment = db.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.id == str(payment_id), PaymentAttempt.order_id == order.id
        )
        .execution_options(populate_existing=True)
    )
    if payment is None:
        raise HTTPException(404, "Payment not found")
    return payment


def provider_available(payment):
    if (
        not configured(payment.provider, collection=False)
        or payment.mode != settings.PAYMENT_MODE
        or payment.account_fingerprint != fingerprint(payment.provider)
    ):
        raise HTTPException(
            503, "The original payment provider configuration is required"
        )


def log(db, order, payment, kind, message, actor=None):
    payment.updated_at = utcnow()
    db.add(PaymentEvent(payment_id=payment.id, kind=kind, message=message))
    event(db, order, actor, kind, message)


def public_payment(db, payment):
    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.payment_id == payment.id)
        .execution_options(populate_existing=True)
    )
    return {
        "id": payment.id,
        "provider": payment.provider,
        "mode": payment.mode,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "status": payment.status,
        "refunded_minor": payment.refunded_minor,
        "checkout_url": payment.checkout_url if payment.status == "pending" else None,
        "phone_hint": "…" + payment.phone[-4:] if payment.phone else None,
        "created_at": payment.created_at,
        "paid_at": payment.paid_at,
        "checked_at": payment.checked_at,
        "refund": {
            "id": refund.id,
            "status": refund.status,
            "amount_minor": refund.amount_minor,
            "reason": refund.reason,
        }
        if refund
        else None,
    }


def eligibility(db, order):
    problems = preparation_blockers(db, order, include_deferred=False)
    items = rows(db, OrderItem, order.id)
    if not items or any(
        item.fulfillment_status not in ("processing", "ready") for item in items
    ):
        problems.append("The registrar must approve every document before payment.")
    if order.payment_status != "not_started":
        problems.append("An existing payment or refund must be resolved first.")
    if (order.submitted_snapshot or {}).get("total_minor", 0) <= 0:
        problems.append("This order has no payable fee.")
    return problems


def create_attempt(db, user, order, payload, key):
    request_hash = digest({"provider": payload.provider, "phone": payload.phone})
    existing = db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.order_id == order.id, PaymentAttempt.idempotency_key == key
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "This payment key was used with different details")
        return existing
    check_version(order.version, payload.expected_version)
    if order.institution_id != settings.PAYMENT_INSTITUTION_ID or not configured(
        payload.provider
    ):
        raise HTTPException(
            503, "This payment method is not configured for this institution"
        )
    problems = eligibility(db, order)
    if problems:
        raise HTTPException(
            409, {"message": "Payment is not available", "fields": problems}
        )
    amount = order.submitted_snapshot["total_minor"]
    if payload.provider == "mpesa" and amount % 100:
        raise HTTPException(
            422,
            "M-Pesa requires a whole-shilling total. Use card payment; fees will not be rounded.",
        )
    payment = PaymentAttempt(
        id=str(uuid4()),
        order_id=order.id,
        active_order_id=order.id,
        provider=payload.provider,
        mode=settings.PAYMENT_MODE,
        account_fingerprint=fingerprint(payload.provider),
        idempotency_key=key,
        request_hash=request_hash,
        amount_minor=amount,
        phone=payload.phone,
        request_data={},
    )
    if payment.provider == "stripe":
        origin = settings.PAYMENT_PUBLIC_URL.rstrip("/")
        payment.request_data = {
            "mode": "payment",
            "payment_method_types[0]": "card",
            "line_items[0][price_data][currency]": "kes",
            "line_items[0][price_data][unit_amount]": str(amount),
            "line_items[0][price_data][product_data][name]": "Academic document order "
            + order.reference,
            "line_items[0][quantity]": "1",
            "client_reference_id": payment.id,
            "metadata[payment_id]": payment.id,
            "payment_intent_data[metadata][payment_id]": payment.id,
            "success_url": origin + "/workspace?payment_return=1",
            "cancel_url": origin + "/workspace?payment_cancel=1",
        }
    else:
        payment.request_data = {
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount // 100,
            "PartyA": payment.phone,
            "PartyB": settings.MPESA_SHORTCODE,
            "PhoneNumber": payment.phone,
            "CallBackURL": callback_url("payments", payment.id),
            "AccountReference": payment.id.replace("-", "")[:12],
            "TransactionDesc": "Document order",
        }
    db.add(payment)
    db.flush()
    order.payment_status = "pending"
    log(
        db,
        order,
        payment,
        "payment_created",
        "Payment initiated; confirmation is pending.",
        user.id,
    )
    db.commit()  # Persist the reservation before making a potentially irreversible provider request.
    return payment


def dispatch(db, user, order_id, payment_id, gateway, *, retry=False):
    order = get_order(db, user, order_id, lock=True)
    payment = payment_for(db, order, payment_id)
    if payment.status not in OPEN or payment.provider_reference:
        return payment
    provider_available(payment)
    if payment.dispatched_at:
        if not retry:
            return payment
        if payment.provider != "stripe" or utcnow() - aware(
            payment.created_at
        ) >= timedelta(hours=23):
            raise HTTPException(
                409,
                "Do not resend this uncertain request. Provider investigation is required.",
            )
    payment.dispatched_at = utcnow()
    payment.status = "unknown"  # A crash after this commit must not cause an automatic second STK push.
    db.commit()
    try:
        result = gateway.initiate(payment)
    except (GatewayUnavailable, GatewayRejected) as exc:
        order = get_order(db, user, order_id, lock=True)
        payment = payment_for(db, order, payment_id)
        if payment.status in OPEN:
            if isinstance(exc, GatewayRejected):
                payment.status = "failed"
                payment.active_order_id = None
                order.payment_status = aggregate_status(db, order)
            log(
                db,
                order,
                payment,
                "payment_unconfirmed",
                "The provider request is unconfirmed. Refresh status before trying another payment.",
            )
        db.commit()
        return payment
    order = get_order(db, user, order_id, lock=True)
    payment = payment_for(db, order, payment_id)
    if payment.provider_reference and payment.provider_reference != result["reference"]:
        raise HTTPException(
            409, "Provider reference mismatch; reconciliation is required"
        )
    payment.provider_reference = result["reference"]
    payment.checkout_url = result.get("checkout_url")
    if payment.status in OPEN:
        payment.status = "pending"
    db.commit()
    return payment


def ledger_entry(db, payment, key, amount):
    existing = db.scalar(
        select(PaymentLedger).where(
            PaymentLedger.payment_id == payment.id, PaymentLedger.entry_key == key
        )
    )
    if existing is None:
        db.add(PaymentLedger(payment_id=payment.id, entry_key=key, amount_minor=amount))


def aggregate_status(db, order):
    """Derive the order balance from all attempts, including retries and late callbacks."""
    db.flush()
    attempts = db.scalars(
        select(PaymentAttempt).where(PaymentAttempt.order_id == order.id)
    ).all()
    if any(p.status == "disputed" for p in attempts):
        return "disputed"
    funded = [p for p in attempts if p.paid_at and p.refunded_minor < p.amount_minor]
    pending = [p for p in attempts if p.status in OPEN]
    if len(funded) > 1 or (funded and pending):
        return "review_required"
    if pending:
        return "pending"
    if funded:
        payment = funded[0]
        if payment.refunded_minor:
            return "partially_refunded"
        refund = db.scalar(
            select(PaymentRefund).where(PaymentRefund.payment_id == payment.id)
        )
        if refund and refund.status == "requested":
            return "refund_requested"
        if refund and refund.status in ("initiating", "pending", "unknown"):
            return "refund_pending"
        return "paid"
    if any(p.refunded_minor for p in attempts):
        return "refunded"
    return "not_started"


def apply_observation(db, order, payment, observation):
    status = observation["status"]
    payment.checked_at = utcnow()
    if payment.status in SETTLED and status not in SETTLED:
        return  # Delayed failures must never overwrite a confirmed payment.
    if status not in OPEN | SETTLED | {"failed", "expired"}:
        raise HTTPException(502, "Invalid provider payment status")
    reference = observation.get("transaction_reference")
    if reference:
        duplicate = db.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.provider == payment.provider,
                PaymentAttempt.transaction_reference == reference,
                PaymentAttempt.id != payment.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                409, "Provider transaction reference requires investigation"
            )
    previous = (payment.status, payment.refunded_minor)
    refunded = observation.get("refunded_minor", 0)
    if (
        type(refunded) is not int
        or not payment.refunded_minor <= refunded <= payment.amount_minor
    ):
        raise HTTPException(502, "Provider refund totals require reconciliation")
    other_live = (
        db.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.order_id == order.id,
                PaymentAttempt.id != payment.id,
                PaymentAttempt.status.in_(OPEN | SETTLED),
                PaymentAttempt.status != "refunded",
            )
        )
        if status in SETTLED
        else None
    )
    if status in SETTLED:
        ledger_entry(db, payment, "charge", payment.amount_minor)
        if refunded > payment.refunded_minor:
            ledger_entry(
                db,
                payment,
                "refund-total-" + str(refunded),
                -(refunded - payment.refunded_minor),
            )
        payment.paid_at = payment.paid_at or utcnow()
        payment.transaction_reference = (
            observation.get("transaction_reference") or payment.transaction_reference
        )
    if refunded == payment.amount_minor and status != "disputed":
        status = "refunded"
    payment.status = status
    payment.refunded_minor = refunded
    if status in ("failed", "expired"):
        payment.active_order_id = None
        order.payment_status = "not_started"
    elif status in SETTLED:
        order.payment_status = "paid" if status == "succeeded" else status
        refund = db.scalar(
            select(PaymentRefund)
            .where(PaymentRefund.payment_id == payment.id)
            .execution_options(populate_existing=True)
        )
        if (
            refund
            and refund.status in ("requested", "initiating", "pending", "unknown")
            and status == "succeeded"
        ):
            order.payment_status = (
                "refund_requested" if refund.status == "requested" else "refund_pending"
            )
        if status == "refunded":
            if other_live is None:
                order.status = "cancelled"
                for item in rows(db, OrderItem, order.id):
                    item.fulfillment_status = "cancelled"
            if refund:
                refund.status = "succeeded"
                refund.completed_at = utcnow()
    order.payment_status = aggregate_status(db, order)
    if previous != (payment.status, payment.refunded_minor):
        log(
            db,
            order,
            payment,
            "payment_" + status,
            "Provider-confirmed payment status: " + status.replace("_", " ") + ".",
        )


def reconcile(db, order, payment, gateway):
    provider_available(payment)
    if not payment.provider_reference:
        raise HTTPException(
            409,
            "No provider reference was received. Recover the existing card checkout or ask the institution to investigate; do not start another payment.",
        )
    if payment.provider_reference:
        try:
            observation = gateway.observe(payment)
        except GatewayUnavailable as exc:
            raise HTTPException(
                503,
                "Provider verification is unavailable. Payment status has not been changed.",
            ) from exc
        apply_observation(db, order, payment, observation)
    if payment.provider == "mpesa" and payment.status in SETTLED:
        callbacks = db.scalars(
            select(PaymentWebhook).where(
                PaymentWebhook.payment_id == payment.id,
                PaymentWebhook.processed_at.is_(None),
            )
        ).all()
        receipts = {
            entry.payload.get("receipt")
            for entry in callbacks
            if entry.payload.get("receipt")
            and entry.payload.get("reference") == payment.provider_reference
        }
        if len(receipts) > 1 or (
            receipts
            and payment.transaction_reference
            and payment.transaction_reference not in receipts
        ):
            raise HTTPException(409, "M-Pesa receipt mismatch requires investigation")
        if receipts:
            payment.transaction_reference = next(iter(receipts))
            for entry in callbacks:
                if entry.payload.get("receipt") == payment.transaction_reference:
                    entry.processed_at = utcnow()
    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.payment_id == payment.id)
        .execution_options(populate_existing=True)
    )
    if (
        refund
        and refund.status in ("pending", "unknown")
        and payment.provider == "stripe"
        and refund.provider_reference
    ):
        try:
            status = gateway.refund_status(payment, refund)
        except GatewayUnavailable as exc:
            raise HTTPException(503, "Refund verification is unavailable") from exc
        finish_refund(db, order, payment, refund, status)
    db.commit()
    return payment


def request_refund(db, order, payment, user, reason, version, *, approve=False):
    check_version(order.version, version)
    from app.models.issuance import IssuedDocument

    if db.scalar(
        select(IssuedDocument.id).where(
            IssuedDocument.order_id == order.id, IssuedDocument.issued_at.is_not(None)
        )
    ):
        raise HTTPException(
            409, "Issued orders require a separate institutional refund investigation"
        )
    if user.id == order.user_id and approve:
        raise HTTPException(403, "Another manager must approve your refund")
    if (
        order.status != "submitted"
        or payment.status != "succeeded"
        or payment.refunded_minor
    ):
        raise HTTPException(
            409, "Only an unrefunded successful payment can be refunded here"
        )
    if any(
        i.fulfillment_status
        not in ("awaiting_review", "processing", "ready", "rejected")
        for i in rows(db, OrderItem, order.id)
    ):
        raise HTTPException(
            409, "This order has progressed beyond the supported refund stage"
        )
    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.payment_id == payment.id)
        .execution_options(populate_existing=True)
    )
    if refund and refund.status not in ("requested", "rejected"):
        return refund
    if not approve and refund:
        raise HTTPException(
            409,
            "A refund request already exists. Contact your institution through order messages.",
        )
    if approve:
        provider_available(payment)
        if not payment.transaction_reference:
            raise HTTPException(
                409,
                "A verified provider transaction reference is required before refunding",
            )
        if payment.provider == "mpesa" and not (
            settings.MPESA_INITIATOR and settings.MPESA_SECURITY_CREDENTIAL
        ):
            raise HTTPException(503, "M-Pesa reversal credentials must be configured")
    if refund is None:
        refund = PaymentRefund(
            id=str(uuid4()),
            payment_id=payment.id,
            order_id=order.id,
            requested_by=user.id,
            reason=reason,
            amount_minor=payment.amount_minor,
        )
        db.add(refund)
    refund.reason = reason
    refund.status = "initiating" if approve else "requested"
    if approve:
        refund.approved_by = user.id
    order.payment_status = "refund_pending" if approve else "refund_requested"
    log(
        db,
        order,
        payment,
        "refund_approved" if approve else "refund_requested",
        reason,
        user.id,
    )
    db.commit()
    return refund


def finish_refund(db, order, payment, refund, status):
    if refund.status == "succeeded":
        return
    if status == "succeeded":
        apply_observation(
            db,
            order,
            payment,
            {"status": "refunded", "refunded_minor": payment.amount_minor},
        )
        refund.status = "succeeded"
        refund.completed_at = utcnow()
    elif status in ("failed", "canceled"):
        refund.status = "failed"
        order.payment_status = aggregate_status(db, order)
        log(
            db,
            order,
            payment,
            "refund_failed",
            "The provider did not complete the refund; contact the institution.",
        )
    else:
        refund.status = "pending"


def dispatch_refund(db, user, institution_id, order_id, payment_id, gateway):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    payment = payment_for(db, order, payment_id)
    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.payment_id == payment.id)
        .execution_options(populate_existing=True)
    )
    provider_available(payment)
    if refund.dispatched_at:
        if refund.status != "unknown":
            return payment
        if payment.provider != "stripe" or utcnow() - aware(
            refund.dispatched_at
        ) >= timedelta(hours=23):
            raise HTTPException(
                409,
                "The refund outcome requires provider investigation; it cannot safely be resent.",
            )
    refund.dispatched_at = refund.dispatched_at or utcnow()
    refund.status = "unknown"
    db.commit()
    try:
        result = gateway.refund(payment, refund)
    except GatewayRejected:
        order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
        payment = payment_for(db, order, payment_id)
        db.refresh(refund)
        finish_refund(db, order, payment, refund, "failed")
        db.commit()
        return payment
    except GatewayUnavailable:
        return payment  # Unknown outcomes remain reserved and block release and duplicate refunds.
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    payment = payment_for(db, order, payment_id)
    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.payment_id == payment.id)
        .execution_options(populate_existing=True)
    )
    refund.provider_reference = result["reference"]
    refund.correlation_id = result.get("correlation_id")
    finish_refund(db, order, payment, refund, result["status"])
    db.commit()
    if payment.provider == "mpesa":
        order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
        payment = payment_for(db, order, payment_id)
        db.refresh(refund)
        callbacks = db.scalars(
            select(PaymentWebhook).where(
                PaymentWebhook.payment_id == payment.id,
                PaymentWebhook.processed_at.is_(None),
            )
        ).all()
        for record in callbacks:
            if record.payload.get("refund_id") == refund.id:
                consume_reversal(db, order, payment, refund, record)
        db.commit()
    return payment


def consume_reversal(db, order, payment, refund, record):
    if record.processed_at or not refund.provider_reference:
        return
    result = record.payload["result"]
    if (
        result.get("ConversationID") != refund.provider_reference
        or result.get("OriginatorConversationID") != refund.correlation_id
    ):
        raise HTTPException(409, "Reversal response correlation mismatch")
    if record.payload["timeout"]:
        if refund.status not in ("succeeded", "failed"):
            refund.status = "unknown"
    elif str(result.get("ResultCode")) == "0":
        finish_refund(db, order, payment, refund, "succeeded")
    elif result.get("ResultCode") is not None:
        finish_refund(db, order, payment, refund, "failed")
    else:
        raise HTTPException(400, "Missing reversal result")
    record.processed_at = utcnow()
