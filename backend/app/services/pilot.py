"""Aggregate evidence is observational; no provider calls or automatic expansion."""

from sqlalchemy import and_, func, select

from app.models.academic import InstitutionService, OrderingPolicy
from app.models.access import InstitutionMembership, MembershipRole
from app.models.institution import Institution
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.orders import Order, OrderItem
from app.models.payments import PaymentAttempt
from app.models.pilot import InstitutionOnboarding
from app.models.user import User
from app.schemas.pilot import AcceptanceEvidence
from app.services.operations import exists_for, summary
from app.services.order_timing import as_utc
from app.services.orders import utcnow

ACCEPTANCE_LABELS = {
    "authority": "Institution authority and named approving officer",
    "privacy": "Consent, access controls and data handling",
    "staff_training": "Registrar training and workflow acceptance",
    "support_ownership": "Named support owner and escalation process",
    "payment_acceptance": "Provider payment, callback, reconciliation and refund acceptance",
    "delivery_acceptance": "Scanning, recipient delivery, download and revocation acceptance",
    "recovery_and_monitoring": "Backup restoration, worker monitoring and incident ownership",
}


def evidence_blockers(evidence):
    return [
        f"Evidence required: {label} (at least 20 characters)."
        for key, label in ACCEPTANCE_LABELS.items()
        if len(evidence.get(key, "").strip()) < 20
    ]


def setup_blockers(db, institution_id):
    blockers = []
    manager = db.scalar(
        select(InstitutionMembership.id)
        .join(User)
        .where(
            InstitutionMembership.institution_id == institution_id,
            InstitutionMembership.role == MembershipRole.MANAGER,
            InstitutionMembership.is_active.is_(True),
            User.is_email_verified.is_(True),
        )
        .limit(1)
    )
    if not manager:
        blockers.append("An active verified institution manager is required.")
    if not db.scalar(
        select(InstitutionService.id)
        .where(
            InstitutionService.institution_id == institution_id,
            InstitutionService.is_active.is_(True),
        )
        .limit(1)
    ):
        blockers.append("At least one active document service is required.")
    policy = db.get(OrderingPolicy, institution_id)
    if not policy or not policy.student_instructions.strip():
        blockers.append("Configure the ordering policy and student instructions.")
    return blockers


def onboarding_view(db, institution_id):
    profile = db.get(InstitutionOnboarding, institution_id)
    evidence = AcceptanceEvidence.model_validate(
        profile.evidence if profile else {}
    ).model_dump()
    return {
        "institution_id": institution_id,
        "version": profile.version if profile else 0,
        "status": profile.status if profile else "not_started",
        "evidence": evidence,
        "requirements": ACCEPTANCE_LABELS,
        "review_reason": profile.review_reason if profile else None,
        "submitted_by": profile.submitted_by if profile else None,
        "reviewed_by": profile.reviewed_by if profile else None,
        "blockers": evidence_blockers(evidence) + setup_blockers(db, institution_id),
        "legacy_approval": profile is None
        and db.get(Institution, institution_id).is_approved,
    }


def evaluation_snapshot(db, institution_id, window):
    # Cohort membership is historical, outcomes are observed NOW, not reconstructed at window_end.
    scope = [
        Order.institution_id == institution_id,
        Order.submitted_at >= as_utc(window.window_start),
        Order.submitted_at < as_utc(window.window_end),
    ]
    paid = and_(
        Order.payment_status == "paid",
        exists_for(
            PaymentAttempt,
            PaymentAttempt.status == "succeeded",
            PaymentAttempt.mode == ("live" if window.mode == "live" else "test"),
            PaymentAttempt.paid_at.is_not(None),
            PaymentAttempt.refunded_minor == 0,
        ),
    )
    delivered_item = (
        select(IssuedDocument.id)
        .join(DocumentDelivery, DocumentDelivery.document_id == IssuedDocument.id)
        .where(
            IssuedDocument.item_id == OrderItem.id,
            IssuedDocument.order_id == OrderItem.order_id,
            DocumentDelivery.order_id == OrderItem.order_id,
            IssuedDocument.mode == window.mode,
            IssuedDocument.issued_at.is_not(None),
            IssuedDocument.revoked_at.is_(None),
            DocumentDelivery.download_count > 0,
        )
        .correlate(OrderItem)
        .exists()
    )
    all_delivered = and_(
        exists_for(OrderItem),
        ~exists_for(
            OrderItem,
            ~and_(OrderItem.fulfillment_status == "delivered", delivered_item),
        ),
    )

    def count(*conditions):
        return db.scalar(select(func.count(Order.id)).where(*scope, *conditions))

    counts = {
        "submitted_orders": count(),
        "paid_orders": count(paid),
        "fully_downloaded_orders": count(Order.status == "submitted", all_delivered),
        "paid_and_fully_downloaded_orders": count(
            Order.status == "submitted", paid, all_delivered
        ),
    }
    operations = summary(db, institution_id)
    profile = db.get(InstitutionOnboarding, institution_id)
    institution = db.get(Institution, institution_id)
    blockers = setup_blockers(db, institution_id)
    if not profile or profile.status != "approved":
        blockers.append("Complete the institution onboarding review.")
    if not institution.is_active or not institution.is_approved:
        blockers.append("The institution must be active and approved.")
    if window.mode != "live":
        blockers.append("Demo activity cannot establish live pilot acceptance.")
    if counts["paid_and_fully_downloaded_orders"] == 0:
        blockers.append(
            "At least one paid order with every item issued and downloaded in the selected mode is required."
        )
    for key in ("payments", "deliveries", "cancellations"):
        if operations["counts"][key]:
            blockers.append(
                f"Resolve current institution {key} exceptions before expansion."
            )
    return {
        "captured_at": utcnow().isoformat(),
        "mode": window.mode,
        "window_start": window.window_start.isoformat(),
        "window_end": window.window_end.isoformat(),
        "counts": counts,
        "current_operations": operations["counts"],
        "expansion_blockers": blockers,
        "measurement": "Orders submitted in [start, end); outcomes observed at capture time. Paid/downloaded counts use the selected mode; submitted total includes all modes. Counts are distinct orders, and full download requires every item. Current exceptions cover the entire institution.",
        "limitation": "A recorded expansion decision does not enable payments, issuance or additional merchants. One successful order is a minimum evidence gate, not statistical proof of reliability.",
    }
