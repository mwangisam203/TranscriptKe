from fastapi import HTTPException
from sqlalchemy import select

from app.core.permissions import require_academic_staff
from app.models.academic import AcademicRecordLink
from app.models.fulfillment import OrderHold, RegistrarCase, RegistrarEvent
from app.models.institution import Institution
from app.models.orders import OrderConsent, OrderItem, OrderMessage, OrderQuote
from app.models.payments import PaymentAttempt
from app.services.academic import check_version
from app.services.orders import digest, event, get_order, rows


def case_for(db, order):
    case = db.get(RegistrarCase, order.id)
    if case is None:
        case = RegistrarCase(order_id=order.id)
        db.add(case)
        db.flush()
    return case


def actionable(db, user, institution_id, order_id, version):
    order = get_order(db, user, order_id, institution_id=institution_id, lock=True)
    check_version(order.version, version)
    if order.user_id == user.id:
        raise HTTPException(403, "Another registrar must handle your order")
    if order.payment_status in (
        "refund_requested",
        "refund_pending",
        "refunded",
        "partially_refunded",
        "disputed",
        "review_required",
    ):
        raise HTTPException(
            409, "Resolve the payment or refund review before registrar actions"
        )
    if order.status != "submitted":
        raise HTTPException(
            409, "Only submitted orders without a pending cancellation can be processed"
        )
    if not db.get(Institution, institution_id).is_approved:
        raise HTTPException(
            409, "Institution approval is required for registrar actions"
        )
    return order


def assigned_case(db, user, order):
    case = case_for(db, order)
    if case.assigned_to != user.id:
        raise HTTPException(
            409, "Claim this order or ask a manager to assign it to you first"
        )
    return case


def record(db, order, user, action, student_message, internal_note):
    event(db, order, user.id, action, student_message)
    db.add(
        RegistrarEvent(
            order_id=order.id,
            order_version=order.version,
            actor_id=user.id,
            action=action,
            internal_note=internal_note,
        )
    )


def preparation_blockers(db, order, *, include_deferred=True):
    blockers = []
    if order.payment_status in (
        "refund_requested",
        "refund_pending",
        "refunded",
        "partially_refunded",
        "disputed",
        "review_required",
    ):
        blockers.append("Resolve the payment or refund review before fulfillment.")
    institution = db.get(Institution, order.institution_id)
    if not institution.is_active or not institution.is_approved:
        blockers.append("Institution approval is required.")
    if order.status != "submitted":
        blockers.append("Order must be submitted without a pending cancellation.")
    link = db.get(AcademicRecordLink, order.academic_record_link_id)
    snapshot = order.submitted_snapshot or {}
    if (
        not link
        or link.status != "matched"
        or link.user_id != order.user_id
        or link.version != snapshot.get("academic_record", {}).get("version")
    ):
        blockers.append(
            "The academic record match has changed; a new authorized order is required."
        )
    consent = (
        db.get(OrderConsent, order.submission_consent_id)
        if order.submission_consent_id
        else None
    )
    quote = (
        db.get(OrderQuote, order.submission_quote_id)
        if order.submission_quote_id
        else None
    )
    if (
        not consent
        or not quote
        or consent.order_id != order.id
        or consent.user_id != order.user_id
        or consent.quote_id != quote.id
        or quote.order_id != order.id
        or consent.scope_hash != digest(snapshot)
        or quote.scope_hash != consent.scope_hash
    ):
        blockers.append("Valid consent for the submitted order is required.")
    if any(h.resolved_at is None for h in rows(db, OrderHold, order.id)):
        blockers.append("Resolve all active holds.")
    if any(
        m.requires_response and m.answered_at is None
        for m in rows(db, OrderMessage, order.id)
    ):
        blockers.append("The student must answer outstanding information requests.")
    case = db.get(RegistrarCase, order.id)
    if (
        include_deferred
        and order.release_when != "now"
        and (not case or case.release_confirmed_at is None)
    ):
        blockers.append(
            "Confirm the requested grades or graduation event before preparation is completed."
        )
    return blockers


def summary(db, order, *, staff=False):
    holds = rows(db, OrderHold, order.id)
    result = {
        "order_id": order.id,
        "version": order.version,
        "items": [
            {"key": i.key, "status": i.fulfillment_status}
            for i in rows(db, OrderItem, order.id)
        ],
        "holds": [
            {
                "id": h.id,
                "category": h.category,
                "student_message": h.student_message,
                "created_at": h.created_at,
                "resolved_at": h.resolved_at,
                "resolution": h.resolution,
            }
            for h in holds
        ],
    }
    if staff:
        case = db.get(RegistrarCase, order.id)
        blockers = preparation_blockers(db, order)
        release_blockers = list(blockers)
        items = rows(db, OrderItem, order.id)
        if not items or any(i.fulfillment_status != "ready" for i in items):
            release_blockers.append("Every document must be ready.")
        if (
            order.payment_status != "paid"
            and (order.submitted_snapshot or {}).get("total_minor", 1) > 0
        ):
            release_blockers.append("Verified payment is required before release.")
        if order.payment_status == "paid":
            verified = db.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.order_id == order.id,
                    PaymentAttempt.status == "succeeded",
                    PaymentAttempt.paid_at.is_not(None),
                    PaymentAttempt.refunded_minor == 0,
                )
            )
            if verified is None or verified.amount_minor != (
                order.submitted_snapshot or {}
            ).get("total_minor"):
                release_blockers.append(
                    "A matching provider-confirmed payment is required."
                )
            elif verified.mode != "live":
                release_blockers.append(
                    "Test payments cannot authorize production issuance."
                )
        release_blockers.append("Secure issuance and delivery are not implemented yet.")
        consent = (
            db.get(OrderConsent, order.submission_consent_id)
            if order.submission_consent_id
            else None
        )
        result.update(
            authorization={
                "text": consent.text,
                "accepted_at": consent.accepted_at,
                "text_version": consent.text_version,
            }
            if consent and consent.order_id == order.id
            else None,
            assigned_to=case.assigned_to if case else None,
            release_confirmed_at=case.release_confirmed_at if case else None,
            preparation_blockers=blockers,
            release_blockers=release_blockers,
            can_release=False,
            history=[
                {
                    "action": e.action,
                    "actor_id": e.actor_id,
                    "internal_note": e.internal_note,
                    "created_at": e.created_at,
                    "order_version": e.order_version,
                }
                for e in rows(db, RegistrarEvent, order.id)
            ],
        )
    return result


def assign(db, user, order, target):
    case = case_for(db, order)
    if target != user.id or case.assigned_to not in (None, user.id):
        require_academic_staff(db, user, order.institution_id, manage=True)
    if target is not None:
        if target == order.user_id:
            raise HTTPException(403, "An order cannot be assigned to its owner")
        from app.models.user import User

        assignee = db.get(User, target)
        if assignee is None:
            raise HTTPException(422, "Assignee not found")
        require_academic_staff(db, assignee, order.institution_id)
    case.assigned_to = target
    record(
        db,
        order,
        user,
        "registrar_assigned",
        "Institutional review assignment updated.",
        f"Assigned to account {target}.",
    )
