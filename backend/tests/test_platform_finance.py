"""A platform merchant collects one school's order at a time; only finance admins refund."""

# ruff: noqa: F811 -- shared pytest fixtures
import pytest

from app.core.config import Settings, settings
from app.models.billing import InstitutionBilling
from app.models.orders import Order
from app.models.user import UserRole
from tests.test_orders import checked, submit
from tests.test_payments import gateway, start  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401


@pytest.fixture
def platform(upfront, monkeypatch, db, catalog):
    monkeypatch.setattr(settings, "PAYMENT_ROUTING_MODE", "platform")
    monkeypatch.setattr(settings, "PAYMENT_INSTITUTION_ID", None)
    db.add(InstitutionBilling(institution_id=catalog["institution"].id, enabled=True))
    db.commit()
    return upfront


def paid(client, w, gateway):
    submit(client, w)
    payment = checked(start(client, w), 201)
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "transaction_reference": "pi_platform_" + payment["id"],
        "refunded_minor": 0,
    }
    checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    return payment


def test_platform_collection_does_not_need_environment_school_id(client, platform):
    submit(client, platform)
    payment = checked(start(client, platform, provider="mpesa"), 201)
    assert payment["merchant_scope"] == "platform"
    assert payment["status"] == "pending"
    config = Settings(
        _env_file=None,
        DATABASE_URL="sqlite://",
        SECRET_KEY="test-secret-at-least-thirty-two-characters",
        PAYMENTS_ENABLED=True,
        PAYMENT_ROUTING_MODE="platform",
        PAYMENT_INSTITUTION_ID=None,
    )
    assert config.PAYMENT_INSTITUTION_ID is None


def test_disabled_school_blocks_new_collection_but_not_reconciliation(
    client, platform, gateway, db, catalog
):
    w = platform
    submit(client, w)
    payment = checked(start(client, w), 201)
    billing = db.get(InstitutionBilling, catalog["institution"].id)
    billing.enabled = False
    db.commit()
    assert (
        checked(client.get(w["pay_url"], headers=w["headers"]))["available_methods"]
        == []
    )
    assert (
        client.post(
            w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"]
        ).status_code
        == 200
    )


def test_platform_refunds_require_admin_not_school_membership(
    client, platform, gateway, user_factory, auth_headers, catalog, db
):
    w = platform
    payment = paid(client, w, gateway)
    admin = user_factory("finance@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)
    order = checked(client.get(w["url"], headers=w["headers"]))
    payload = {
        "expected_version": order["version"],
        "reason": "Documents not available; please refund",
    }
    requested = checked(
        client.post(
            w["pay_url"] + f"/{payment['id']}/refund-requests",
            headers=w["headers"],
            json=payload,
        )
    )
    assert requested["refund"]["status"] == "requested"
    order = checked(client.get(w["url"], headers=w["headers"]))
    payload["expected_version"] = order["version"]
    school_base = w["staff_url"] + f"/payments/{payment['id']}"
    assert (
        client.post(
            school_base + "/refunds",
            headers=auth_headers(catalog["manager"]),
            json=payload,
        ).status_code
        == 403
    )
    admin_base = f"/api/v1/admin/finance/orders/{order['id']}/payments/{payment['id']}"
    assert (
        client.post(
            admin_base + "/refunds", headers=w["staff_headers"], json=payload
        ).status_code
        == 403
    )
    result = checked(
        client.post(admin_base + "/refunds", headers=headers, json=payload)
    )
    assert result["refund"]["status"] == "succeeded"
    assert gateway.refunds == [result["refund"]["id"]]
    summary = checked(client.get("/api/v1/admin/finance/collections", headers=headers))
    assert summary[0]["net_minor"] == 0
    assert "name_on_record" not in str(
        checked(client.get("/api/v1/admin/finance/payments", headers=headers))
    )
    assert client.get(w["staff_url"], headers=headers).status_code == 403
    db.expire_all()
    assert db.get(Order, order["id"]).status == "cancelled"


def test_billing_switch_is_admin_only_audited_and_versioned(
    client, platform, catalog, auth_headers, user_factory
):
    path = f"/api/v1/admin/institutions/{catalog['institution'].id}/billing"
    assert client.get(path, headers=platform["headers"]).status_code == 403
    assert (
        client.put(
            path,
            headers=auth_headers(catalog["manager"]),
            json={
                "enabled": False,
                "expected_version": 1,
                "reason": "Pause collection",
            },
        ).status_code
        == 403
    )
    admin = user_factory("billing-admin@example.com", role=UserRole.ADMIN)
    payload = {"enabled": False, "expected_version": 1, "reason": "Pause collection"}
    headers = auth_headers(admin)
    assert checked(client.put(path, headers=headers, json=payload))["version"] == 2
    assert client.put(path, headers=headers, json=payload).status_code == 409


def test_platform_supports_different_schools_with_separate_single_school_orders(
    client, platform, db, institutions, auth_headers, gateway
):
    from app.models.academic import InstitutionService, OrderingPolicy
    from tests.fixtures_academic import POLICY, SERVICE
    from tests.test_academic_records import create
    from tests.test_orders import RECIPIENT

    second_school = institutions[1]
    service = InstitutionService(institution_id=second_school.id, **SERVICE)
    policy = OrderingPolicy(
        institution_id=second_school.id,
        **{key: value for key, value in POLICY.items() if key != "expected_version"},
    )
    db.add_all(
        [
            service,
            policy,
            InstitutionBilling(institution_id=second_school.id, enabled=True),
        ]
    )
    db.commit()
    link = create(
        client,
        {"institution": second_school, "service": service},
        platform["user"],
        auth_headers,
    )
    headers = {
        **auth_headers(platform["user"]),
        "Idempotency-Key": "second-school-order",
    }
    order = checked(
        client.post(
            "/api/v1/orders",
            headers=headers,
            json={"academic_record_link_id": link["id"]},
        ),
        201,
    )
    url = f"/api/v1/orders/{order['id']}"
    checked(
        client.put(
            url,
            headers=headers,
            json={
                "expected_version": order["version"],
                "purpose": "Admissions",
                "recipients": [RECIPIENT],
                "items": [
                    {
                        "key": "item1",
                        "service_id": service.id,
                        "recipient_key": "recipient1",
                        "quantity": 2,
                    }
                ],
            },
        )
    )
    second = {"url": url, "pay_url": url + "/payments", "headers": headers}
    first_payment = paid(client, platform, gateway)
    second_payment = paid(client, second, gateway)
    assert (
        first_payment["merchant_scope"]
        == second_payment["merchant_scope"]
        == "platform"
    )
    first_order = checked(client.get(platform["url"], headers=platform["headers"]))
    second_order = checked(client.get(url, headers=headers))
    assert first_order["institution_id"] != second_order["institution_id"]
    assert second_order["institution_id"] == second_school.id
    assert second_order["status"] == first_order["status"] == "submitted"
    assert (
        client.post(
            url + f"/payments/{first_payment['id']}/reconcile", headers=headers
        ).status_code
        == 404
    )


def test_unconfirmed_payment_review_is_admin_only_and_allows_retry(
    client, platform, gateway, user_factory, auth_headers, db
):
    from sqlalchemy import select

    from app.models.access import AccessEvent
    from app.models.payments import PaymentAttempt

    w = platform
    submit(client, w)
    gateway.fail_start = True
    payment = checked(start(client, w, provider="mpesa"), 201)
    assert not payment["can_check_status"] and not payment["can_resume"]
    url = f"/api/v1/admin/finance/orders/{w['order']['id']}/payments/{payment['id']}/no-payment-review"
    body = {
        "expected_version": checked(client.get(w["url"], headers=w["headers"]))[
            "version"
        ],
        "provider_case_reference": "CASE-2026-TEST",
        "evidence": "Provider confirmed this request was not accepted and no payment was received.",
        "confirmed_no_payment": True,
    }
    assert client.post(url, headers=w["headers"], json=body).status_code == 403
    admin = auth_headers(user_factory("reviewer@example.com", role=UserRole.ADMIN))
    assert (
        client.post(
            url, headers=admin, json={**body, "confirmed_no_payment": False}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, headers=admin, json={**body, "expected_version": 1}
        ).status_code
        == 409
    )
    reviewed = checked(client.post(url, headers=admin, json=body))
    assert reviewed["status"] == "failed"
    assert db.get(PaymentAttempt, payment["id"]).active_order_id is None
    audit = db.scalar(
        select(AccessEvent).where(AccessEvent.action == "unconfirmed_payment_reviewed")
    )
    assert audit.details["provider_case_reference"] == "CASE-2026-TEST"
    assert "CASE-2026-TEST" not in str(reviewed)
    assert client.post(url, headers=admin, json=body).status_code == 409
    gateway.fail_start = False
    retry = checked(start(client, w, provider="mpesa", key="after-investigation"), 201)
    assert retry["id"] != payment["id"] and retry["status"] == "pending"
    assert checked(client.get(w["url"], headers=w["headers"]))["submitted_at"] is None


def test_review_cannot_override_a_provider_accepted_payment(
    client, platform, gateway, user_factory, auth_headers
):
    w = platform
    submit(client, w)
    payment = checked(start(client, w, provider="mpesa"), 201)
    url = f"/api/v1/admin/finance/orders/{w['order']['id']}/payments/{payment['id']}/no-payment-review"
    body = {
        "expected_version": checked(client.get(w["url"], headers=w["headers"]))[
            "version"
        ],
        "provider_case_reference": "CASE-TEST",
        "evidence": "Provider investigation must not replace querying an accepted payment.",
        "confirmed_no_payment": True,
    }
    admin = auth_headers(
        user_factory("accepted-reviewer@example.com", role=UserRole.ADMIN)
    )
    assert client.post(url, headers=admin, json=body).status_code == 409
