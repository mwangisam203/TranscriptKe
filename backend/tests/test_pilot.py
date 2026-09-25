"""Acceptance gates, immutable evidence and tenant isolation. Providers stay fake."""

# Imported fixtures intentionally share names with test arguments.
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models.access import AccessEvent, InstitutionMembership, MembershipRole
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.orders import Order, OrderItem
from app.models.payments import PaymentAttempt
from app.models.pilot import InstitutionOnboarding, PilotEvaluation
from app.models.user import UserRole
from app.services.orders import utcnow
from app.services.pilot import ACCEPTANCE_LABELS
from tests.fixtures_academic import grant
from tests.test_issuance import code, issued, ready, registrar  # noqa: F401
from tests.test_orders import checked, workflow  # noqa: F401
from tests.test_payments import gateway  # noqa: F401

EVIDENCE = {
    key: f"Test evidence for {key}: reviewed by pilot owner with dated acceptance reference."
    for key in ACCEPTANCE_LABELS
}
REASON = "Reviewed evidence with institution owner and documented the next steps."


def window(mode="demo"):
    return {
        "mode": mode,
        "window_start": (utcnow() - timedelta(days=30)).isoformat(),
        "window_end": utcnow().isoformat(),
    }


@pytest.fixture
def pilot(catalog, user_factory, auth_headers):
    admin = user_factory("pilot-admin@example.com", role=UserRole.ADMIN)
    id = catalog["institution"].id
    return {
        "staff": f"/api/v1/staff/institutions/{id}",
        "admin": f"/api/v1/admin/institutions/{id}",
        "mh": auth_headers(catalog["manager"]),
        "ah": auth_headers(admin),
        "admin_user": admin,
        "id": id,
    }


def approved_onboarding(client, p):
    data = checked(client.get(p["staff"] + "/onboarding", headers=p["mh"]))
    saved = checked(
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": data["version"], "evidence": EVIDENCE},
        )
    )
    submitted = checked(
        client.post(
            p["staff"] + "/onboarding/submit",
            headers=p["mh"],
            json={"expected_version": saved["version"]},
        )
    )
    return checked(
        client.post(
            p["admin"] + "/onboarding/review",
            headers=p["ah"],
            json={
                "expected_version": submitted["version"],
                "decision": "approved",
                "reason": REASON,
            },
        )
    )


def evaluation(client, p, mode="demo"):
    return checked(
        client.post(
            p["staff"] + "/pilot/evaluations",
            headers=p["mh"],
            json={**window(mode), "evidence": EVIDENCE, "findings": REASON},
        ),
        201,
    )


def review(client, p, item, decision="expand"):
    return client.post(
        p["admin"] + f"/pilot/evaluations/{item['id']}/review",
        headers=p["ah"],
        json={
            "expected_version": item["version"],
            "decision": decision,
            "reason": REASON,
        },
    )


def live_evidence(db, issued):
    # Synthetic acceptance data: never call a live provider in automated tests.
    payment = db.scalar(select(PaymentAttempt))
    document = db.scalar(select(IssuedDocument))
    delivery = db.scalar(select(DocumentDelivery))
    payment.mode, document.mode = "live", "live"
    db.get(OrderItem, document.item_id).fulfillment_status = "delivered"
    delivery.download_count, delivery.last_downloaded_at, delivery.notified_at = (
        1,
        utcnow(),
        utcnow(),
    )
    db.commit()
    return document, delivery


def test_create_unapproved_institution_and_prevent_approval_bypass(client, pilot, db):
    p = pilot
    body = {"name": "New Pilot College", "code": " new-college "}
    assert (
        client.post(
            "/api/v1/admin/institutions", headers=p["mh"], json=body
        ).status_code
        == 403
    )
    new = checked(
        client.post("/api/v1/admin/institutions", headers=p["ah"], json=body), 201
    )
    assert new["code"] == "NEW-COLLEGE" and not new["is_approved"]
    assert client.get(f"/api/v1/institutions/{new['id']}").status_code == 404
    assert db.get(InstitutionOnboarding, new["id"]).status == "draft"
    assert (
        client.post(
            "/api/v1/admin/institutions", headers=p["ah"], json=body
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/admin/institutions",
            headers=p["ah"],
            json={**body, "is_approved": True},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/v1/admin/institutions/{new['id']}/approval",
            headers=p["ah"],
            json={"approved": True, "expected_approved": False, "reason": REASON},
        ).status_code
        == 409
    )


def test_onboarding_versions_feedback_audit_and_approval(client, pilot, catalog, db):
    p = pilot
    catalog["institution"].is_approved = False
    db.commit()
    saved = checked(
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": 0, "evidence": EVIDENCE},
        )
    )
    assert saved["version"] == 1
    assert (
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": 0, "evidence": EVIDENCE},
        ).status_code
        == 409
    )
    submitted = checked(
        client.post(
            p["staff"] + "/onboarding/submit",
            headers=p["mh"],
            json={"expected_version": 1},
        )
    )
    assert submitted["status"] == "submitted"
    assert (
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": 2, "evidence": EVIDENCE},
        ).status_code
        == 409
    )
    returned = checked(
        client.post(
            p["admin"] + "/onboarding/review",
            headers=p["ah"],
            json={
                "expected_version": 2,
                "decision": "changes_requested",
                "reason": REASON,
            },
        )
    )
    assert returned["status"] == "changes_requested"
    accepted = approved_onboarding(client, p)
    assert accepted["status"] == "approved"
    assert client.get(f"/api/v1/institutions/{p['id']}").status_code == 404
    checked(
        client.put(
            p["admin"] + "/approval",
            headers=p["ah"],
            json={"approved": True, "expected_approved": False, "reason": REASON},
        )
    )
    history = checked(client.get(p["admin"] + "/onboarding", headers=p["ah"]))[
        "history"
    ]
    assert len(history) == 6
    assert history[0]["action"] == "onboarding_reviewed"
    assert any(e["details"].get("evidence") == EVIDENCE for e in history)
    assert (
        client.get(p["admin"] + "/onboarding", headers=p["ah"]).headers["cache-control"]
        == "no-store"
    )


def test_incomplete_evidence_and_changed_setup_block_submission_and_review(
    client, pilot, catalog, db
):
    p = pilot
    checked(
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": 0, "evidence": {}},
        )
    )
    assert (
        client.post(
            p["staff"] + "/onboarding/submit",
            headers=p["mh"],
            json={"expected_version": 1},
        ).status_code
        == 409
    )
    checked(
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": 1, "evidence": EVIDENCE},
        )
    )
    checked(
        client.post(
            p["staff"] + "/onboarding/submit",
            headers=p["mh"],
            json={"expected_version": 2},
        )
    )
    catalog["service"].is_active = False
    db.commit()
    assert (
        client.post(
            p["admin"] + "/onboarding/review",
            headers=p["ah"],
            json={"expected_version": 3, "decision": "approved", "reason": REASON},
        ).status_code
        == 409
    )
    assert (
        client.post(
            p["staff"] + "/pilot/evaluations",
            headers=p["mh"],
            json={**window(), "evidence": {}, "findings": REASON},
        ).status_code
        == 409
    )


def test_private_pilot_access_requires_manager_or_explicit_admin_route(
    client, pilot, catalog, user_factory, auth_headers, institutions, db
):
    p = pilot
    student = user_factory("pilot-student@example.com")
    for user in (student, catalog["staff"], p["admin_user"]):
        headers = auth_headers(user)
        assert (
            client.get(p["staff"] + "/onboarding", headers=headers).status_code == 403
        )
        assert (
            client.get(p["staff"] + "/pilot/evaluations", headers=headers).status_code
            == 403
        )
        assert (
            client.post(
                p["staff"] + "/pilot/preview", headers=headers, json=window()
            ).status_code
            == 403
        )
    assert client.get(p["admin"] + "/onboarding", headers=p["mh"]).status_code == 403
    foreign = f"/api/v1/staff/institutions/{institutions[1].id}"
    assert client.get(foreign + "/onboarding", headers=p["mh"]).status_code == 403
    member = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.user_id == catalog["manager"].id
        )
    )
    member.is_active = False
    db.commit()
    assert (
        client.put(
            p["staff"] + "/onboarding",
            headers=p["mh"],
            json={"expected_version": 0, "evidence": EVIDENCE},
        ).status_code
        == 403
    )


def test_existing_approvals_are_not_pilot_acceptance(client, pilot):
    p = pilot
    data = checked(client.get(p["staff"] + "/onboarding", headers=p["mh"]))
    assert data["legacy_approval"] and data["status"] == "not_started"
    assert client.get(f"/api/v1/institutions/{p['id']}").status_code == 200
    snapshot = checked(
        client.post(p["staff"] + "/pilot/preview", headers=p["mh"], json=window("live"))
    )
    assert (
        "Complete the institution onboarding review." in snapshot["expansion_blockers"]
    )
    assert snapshot["counts"]["submitted_orders"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "test"},
        {"window_start": "2026-01-01T00:00:00"},
        {"window_end": (utcnow() + timedelta(days=1)).isoformat()},
        {"window_start": (utcnow() - timedelta(days=400)).isoformat()},
        {
            "window_start": utcnow().isoformat(),
            "window_end": (utcnow() - timedelta(days=1)).isoformat(),
        },
        {"institution_id": 999},
    ],
)
def test_invalid_evaluation_window(client, pilot, changes):
    assert (
        client.post(
            pilot["staff"] + "/pilot/preview",
            headers=pilot["mh"],
            json={**window(), **changes},
        ).status_code
        == 422
    )


def test_demo_evaluation_can_continue_but_never_expand(
    client, pilot, issued, db, mailer
):
    p = pilot
    approved_onboarding(client, p)
    delivery = db.scalar(select(DocumentDelivery))
    access_code = code(client, issued, mailer)
    assert (
        client.post(
            issued["delivery_url"] + "/download", json={"code": access_code}
        ).status_code
        == 200
    )
    delivery.notified_at = utcnow()
    db.commit()
    item = evaluation(client, p)
    assert item["snapshot"]["counts"]["paid_and_fully_downloaded_orders"] == 1
    assert review(client, p, item).status_code == 409
    accepted = checked(review(client, p, item, "continue_pilot"))
    assert accepted["snapshot"] == item["snapshot"] and accepted["version"] == 2
    assert review(client, p, item, "rework").status_code == 409
    live = checked(
        client.post(p["staff"] + "/pilot/preview", headers=p["mh"], json=window("live"))
    )
    assert (
        live["counts"]["paid_orders"] == live["counts"]["fully_downloaded_orders"] == 0
    )


def test_live_complete_cohort_can_be_reviewed_without_enabling_features(
    client, pilot, issued, db
):
    p = pilot
    approved_onboarding(client, p)
    live_evidence(db, issued)
    item = evaluation(client, p, "live")
    assert item["snapshot"]["expansion_blockers"] == []
    result = checked(review(client, p, item))
    assert result["decision"] == "expand"
    assert result["snapshot"] == item["snapshot"]
    assert result["review_snapshot"]["counts"]["paid_and_fully_downloaded_orders"] == 1
    assert (
        db.scalar(
            select(func.count(AccessEvent.id)).where(
                AccessEvent.action == "pilot_evaluation_reviewed"
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        "revocation",
        "refund",
        "undownloaded",
        "delivery_failure",
        "approval_removed",
        "extra_item",
    ],
)
def test_expansion_rechecks_current_evidence(
    client, pilot, issued, catalog, db, change
):
    p = pilot
    approved_onboarding(client, p)
    document, delivery = live_evidence(db, issued)
    item = evaluation(client, p, "live")
    assert item["snapshot"]["expansion_blockers"] == []
    if change == "revocation":
        document.revoked_at = utcnow()
    elif change == "refund":
        db.scalar(select(PaymentAttempt)).refunded_minor = 1
    elif change == "undownloaded":
        delivery.download_count = 0
    elif change == "delivery_failure":
        delivery.notified_at, delivery.notification_attempts = None, 1
    elif change == "approval_removed":
        catalog["institution"].is_approved = False
    else:
        first = db.scalar(select(OrderItem))
        from app.models.academic import InstitutionService
        from tests.fixtures_academic import SERVICE

        service = InstitutionService(
            institution_id=p["id"], **{**SERVICE, "code": "EXTRA"}
        )
        db.add(service)
        db.flush()
        db.add(
            OrderItem(
                order_id=first.order_id,
                key="extra",
                service_id=service.id,
                recipient_key=first.recipient_key,
                fulfillment_status="ready",
            )
        )
    db.commit()
    assert review(client, p, item).status_code == 409
    db.expire_all()
    assert db.get(PilotEvaluation, item["id"]).decision == "pending"


def test_tenant_scoped_pagination_no_student_data_and_immutable_snapshots(
    client, pilot, issued, institutions, db
):
    p = pilot
    item = evaluation(client, p)
    evaluation(client, p)
    page = checked(
        client.get(p["admin"] + "/pilot/evaluations?limit=1", headers=p["ah"])
    )
    assert page["total"] == 2 and len(page["items"]) == 1
    other = checked(
        client.get(p["admin"] + "/pilot/evaluations?limit=1&offset=1", headers=p["ah"])
    )
    assert page["items"][0]["id"] != other["items"][0]["id"]
    foreign = {**p, "admin": f"/api/v1/admin/institutions/{institutions[1].id}"}
    assert (
        checked(client.get(foreign["admin"] + "/pilot/evaluations", headers=p["ah"]))[
            "total"
        ]
        == 0
    )
    assert review(client, foreign, item, "rework").status_code == 404
    encoded = str(item["snapshot"])
    assert (
        "admissions@example.com" not in encoded
        and "checkout_url" not in encoded
        and "record_reference" not in encoded
    )
    order = db.scalar(select(Order))
    order.submitted_at = utcnow() - timedelta(days=60)
    db.commit()
    preview = checked(
        client.post(p["staff"] + "/pilot/preview", headers=p["mh"], json=window())
    )
    assert preview["counts"]["submitted_orders"] == 0
    assert db.get(PilotEvaluation, item["id"]).snapshot == item["snapshot"]


def test_admin_manager_cannot_review_own_evidence(client, pilot, catalog, db):
    p = pilot
    grant(db, p["admin_user"], catalog["institution"], MembershipRole.MANAGER)
    own = {**p, "mh": p["ah"]}
    item = evaluation(client, own)
    assert review(client, own, item, "continue_pilot").status_code == 403
    checked(
        client.put(
            p["staff"] + "/onboarding",
            headers=p["ah"],
            json={"expected_version": 0, "evidence": EVIDENCE},
        )
    )
    checked(
        client.post(
            p["staff"] + "/onboarding/submit",
            headers=p["ah"],
            json={"expected_version": 1},
        )
    )
    assert (
        client.post(
            p["admin"] + "/onboarding/review",
            headers=p["ah"],
            json={"expected_version": 2, "decision": "approved", "reason": REASON},
        ).status_code
        == 403
    )


def test_simultaneous_pilot_review_only_records_one_decision(client, pilot, db, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")
    item = evaluation(client, pilot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: review(client, pilot, item, "continue_pilot").status_code,
                range(2),
            )
        )
    assert sorted(results) == [200, 409]
    assert (
        db.scalar(
            select(func.count(AccessEvent.id)).where(
                AccessEvent.action == "pilot_evaluation_reviewed"
            )
        )
        == 1
    )


def test_cohort_utc_offsets_and_exclusive_end(client, pilot, issued, db):
    from datetime import timezone

    order = db.scalar(select(Order))
    submitted = utcnow() - timedelta(days=2)
    order.submitted_at = submitted
    db.commit()
    east_africa = timezone(timedelta(hours=3))
    body = {
        "mode": "demo",
        "window_start": submitted.astimezone(east_africa).isoformat(),
        "window_end": (submitted + timedelta(hours=1))
        .astimezone(east_africa)
        .isoformat(),
    }
    preview = checked(
        client.post(pilot["staff"] + "/pilot/preview", headers=pilot["mh"], json=body)
    )
    assert preview["counts"]["submitted_orders"] == 1
    body["window_start"] = (submitted - timedelta(hours=1)).isoformat()
    body["window_end"] = submitted.isoformat()
    preview = checked(
        client.post(pilot["staff"] + "/pilot/preview", headers=pilot["mh"], json=body)
    )
    assert preview["counts"]["submitted_orders"] == 0


def test_new_institution_can_finish_controlled_onboarding(client, pilot, catalog, db):
    from app.models.institution import Institution
    from tests.fixtures_academic import POLICY, SERVICE

    new = checked(
        client.post(
            "/api/v1/admin/institutions",
            headers=pilot["ah"],
            json={"name": "Expansion College", "code": "EXPAND"},
        ),
        201,
    )
    grant(
        db, catalog["manager"], db.get(Institution, new["id"]), MembershipRole.MANAGER
    )
    p = {
        **pilot,
        "staff": f"/api/v1/staff/institutions/{new['id']}",
        "admin": f"/api/v1/admin/institutions/{new['id']}",
    }
    checked(client.post(p["staff"] + "/services", headers=p["mh"], json=SERVICE), 201)
    checked(client.put(p["staff"] + "/ordering-policy", headers=p["mh"], json=POLICY))
    approved_onboarding(client, p)
    approved = checked(
        client.put(
            p["admin"] + "/approval",
            headers=p["ah"],
            json={"expected_approved": False, "approved": True, "reason": REASON},
        )
    )
    assert approved["is_approved"]
    assert client.get(f"/api/v1/institutions/{new['id']}").status_code == 200
    # Withdrawal stays available even after successful onboarding.
    checked(
        client.put(
            p["admin"] + "/approval",
            headers=p["ah"],
            json={"expected_approved": True, "approved": False, "reason": REASON},
        )
    )
    assert client.get(f"/api/v1/institutions/{new['id']}").status_code == 404


def test_public_approval_rechecks_setup_after_onboarding_acceptance(
    client, pilot, catalog, db
):
    approved_onboarding(client, pilot)
    catalog["institution"].is_approved = False
    catalog["service"].is_active = False
    db.commit()
    response = client.put(
        pilot["admin"] + "/approval",
        headers=pilot["ah"],
        json={"expected_approved": False, "approved": True, "reason": REASON},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["blockers"] == [
        "At least one active document service is required."
    ]
    assert client.get(f"/api/v1/institutions/{pilot['id']}").status_code == 404
