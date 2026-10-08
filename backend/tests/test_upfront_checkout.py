"""Checkout stays private until an authoritative full payment releases it."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.academic import AcademicRecordLink
from app.models.orders import Order, OrderEvent
from app.models.payments import PaymentLedger
from app.services.fulfillment import preparation_blockers
from tests.test_academic_records import create, decision, review_url
from tests.test_orders import RECIPIENT, checked, submit
from tests.test_payments import gateway, start  # noqa: F401 -- shared provider fixture


@pytest.fixture
def upfront(client, catalog, user_factory, auth_headers, monkeypatch, gateway):  # noqa: F811
    monkeypatch.setattr(settings, "PAYMENT_COLLECTION_POLICY", "before_review")
    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    headers = {**auth_headers(user), "Idempotency-Key": "upfront-submit-order"}
    order = checked(
        client.post(
            "/api/v1/orders",
            headers={**headers, "Idempotency-Key": "upfront-order-create"},
            json={"academic_record_link_id": link["id"]},
        ),
        201,
    )
    url = f"/api/v1/orders/{order['id']}"
    order = checked(
        client.put(
            url,
            headers=headers,
            json={
                "expected_version": order["version"],
                "purpose": "Graduate admission",
                "recipients": [RECIPIENT],
                "items": [
                    {
                        "key": "item1",
                        "service_id": catalog["service"].id,
                        "recipient_key": "recipient1",
                        "quantity": 2,
                    }
                ],
            },
        )
    )
    return {
        "user": user,
        "link": link,
        "order": order,
        "headers": headers,
        "url": url,
        "pay_url": url + "/payments",
        "staff_headers": auth_headers(catalog["staff"]),
        "staff_url": f"/api/v1/staff/institutions/{catalog['institution'].id}/orders/{order['id']}",
    }


def test_unpaid_checkout_and_enrollment_are_hidden_from_staff(
    client, upfront, catalog, db
):
    w = upfront
    order, _, payload = submit(client, w)
    assert order["status"] == "awaiting_payment" and order["submitted_at"] is None
    assert order["collection_policy"] == "before_review"
    assert (
        checked(
            client.post(
                w["url"] + "/submit",
                headers={**w["headers"], "Idempotency-Key": "upfront-submit-order"},
                json=payload,
            )
        )["status"]
        == "awaiting_payment"
    )
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
    assert (
        client.get(
            review_url(catalog, w["link"]["id"]), headers=w["staff_headers"]
        ).status_code
        == 404
    )
    assert (
        checked(
            client.get(w["staff_url"].rsplit("/", 1)[0], headers=w["staff_headers"])
        )
        == []
    )
    assert (
        checked(
            client.get(
                review_url(catalog, w["link"]["id"]).rsplit("/", 1)[0],
                headers=w["staff_headers"],
            )
        )
        == []
    )
    assert db.get(Order, order["id"]).processing_due_at is None
    assert checked(client.get(w["pay_url"], headers=w["headers"]))["blockers"] == []


def test_confirmed_payment_releases_once_and_matching_still_blocks_preparation(
    client, upfront, gateway, catalog, db
):  # noqa: F811
    w = upfront
    submit(client, w)
    payment = checked(start(client, w, provider="mpesa"), 201)
    assert payment["status"] == "pending"
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
    reconcile = w["pay_url"] + f"/{payment['id']}/reconcile"
    checked(client.post(reconcile, headers=w["headers"]))
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "transaction_reference": "SANDBOXUPFRONT1",
        "refunded_minor": 0,
    }
    for _ in range(2):
        checked(client.post(reconcile, headers=w["headers"]))
    order = checked(client.get(w["staff_url"], headers=w["staff_headers"]))
    assert order["status"] == "submitted" and order["payment_status"] == "paid"
    assert order["submitted_at"] is not None
    stored = db.get(Order, order["id"])
    db.refresh(stored)
    assert stored.processing_due_at is not None
    assert preparation_blockers(db, stored)
    assert (
        db.scalar(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.order_id == order["id"], OrderEvent.kind == "submitted")
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(PaymentLedger)
            .where(PaymentLedger.payment_id == payment["id"])
        )
        == 1
    )
    checked(
        client.post(
            review_url(catalog, w["link"]["id"]) + "/decisions",
            headers=w["staff_headers"],
            json=decision(),
        )
    )
    db.expire_all()
    assert preparation_blockers(db, db.get(Order, order["id"])) == []


def test_failed_payment_stays_private_and_allows_fresh_attempt(
    client, upfront, gateway
):  # noqa: F811
    w = upfront
    submit(client, w)
    payment = checked(start(client, w, provider="mpesa"), 201)
    gateway.states[payment["id"]] = {"status": "failed", "refunded_minor": 0}
    checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    order = checked(client.get(w["url"], headers=w["headers"]))
    assert (
        order["status"] == "awaiting_payment"
        and order["payment_status"] == "not_started"
    )
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
    assert (
        checked(start(client, w, provider="mpesa", key="second-upfront-payment"), 201)[
            "id"
        ]
        != payment["id"]
    )


def test_zero_fee_checkout_releases_without_provider_charge(
    client, upfront, catalog, db, gateway
):  # noqa: F811
    service = catalog["service"]
    service.fee_minor = 0
    db.commit()
    order, _, _ = submit(client, upfront)
    assert order["status"] == "submitted" and order["submitted_at"]
    assert order["submitted_snapshot"]["total_minor"] == 0
    assert not gateway.starts
    assert (
        client.get(upfront["staff_url"], headers=upfront["staff_headers"]).status_code
        == 200
    )


def test_checkout_scope_changes_and_unpaid_cancellation(client, upfront, db):
    w = upfront
    order, _, _ = submit(client, w)
    link = db.get(AcademicRecordLink, w["link"]["id"])
    link.name_on_record = "Changed enrollment name"
    db.commit()
    assert start(client, w, provider="mpesa").status_code == 409
    cancelled = checked(
        client.post(
            w["url"] + "/cancellation-requests",
            headers=w["headers"],
            json={
                "expected_version": order["version"],
                "reason": "Start a corrected checkout",
            },
        )
    )
    assert cancelled["status"] == "cancelled" and not cancelled["submitted_at"]
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
