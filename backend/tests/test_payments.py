import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.main import app
from app.models.fulfillment import OrderHold
from app.models.orders import Order, OrderItem
from app.models.payments import PaymentAttempt, PaymentLedger, PaymentRefund
from app.services.orders import utcnow
from app.services.payment_gateways import (
    Gateways,
    GatewayUnavailable,
    callback_token,
    get_gateways,
)
from tests.test_orders import checked, submit, workflow  # noqa: F401


class FakeGateways:
    def __init__(self):
        self.starts = []
        self.refunds = []
        self.states = {}
        self.fail_start = False
        self.fail_observe = False
        self.fail_refund = False
        self.refund_result = "succeeded"

    def initiate(self, payment):
        self.starts.append(payment.id)
        if self.fail_start:
            raise GatewayUnavailable
        return {
            "reference": ("cs_" if payment.provider == "stripe" else "ws_")
            + payment.id,
            "checkout_url": "https://checkout.stripe.com/c/test"
            if payment.provider == "stripe"
            else None,
        }

    def observe(self, payment):
        if self.fail_observe:
            raise GatewayUnavailable
        return self.states.get(payment.id, {"status": "pending", "refunded_minor": 0})

    def refund(self, payment, refund):
        self.refunds.append(refund.id)
        if self.fail_refund:
            raise GatewayUnavailable
        return {
            "reference": "re_" + refund.id,
            "correlation_id": "cor_" + refund.id,
            "status": self.refund_result,
        }

    def refund_status(self, payment, refund):
        return self.refund_result


@pytest.fixture
def gateway(monkeypatch, catalog):
    values = {
        "PAYMENTS_ENABLED": True,
        "PAYMENT_MODE": "test",
        "PAYMENT_INSTITUTION_ID": catalog["institution"].id,
        "PAYMENT_PUBLIC_URL": "https://payments.example.com",
        "STRIPE_SECRET_KEY": "sk_test_fake",
        "STRIPE_WEBHOOK_SECRET": "whsec_test",
        "MPESA_CONSUMER_KEY": "test-key",
        "MPESA_CONSUMER_SECRET": "test-secret",
        "MPESA_SHORTCODE": "174379",
        "MPESA_PASSKEY": "test-passkey",
        "MPESA_INITIATOR": "test-initiator",
        "MPESA_SECURITY_CREDENTIAL": "test-encrypted-credential",
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    fake = FakeGateways()
    app.dependency_overrides[get_gateways] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_gateways, None)


@pytest.fixture
def payable(client, workflow, gateway, db):  # noqa: F811
    w = workflow
    submit(client, w)
    item = db.scalar(select(OrderItem).where(OrderItem.order_id == w["order"]["id"]))
    item.fulfillment_status = "processing"
    db.commit()
    w["pay_url"] = w["url"] + "/payments"
    w["staff_pay_url"] = w["staff_url"] + "/payments"
    return w


def start(client, w, provider="stripe", key="payment-create-001"):
    version = checked(client.get(w["url"], headers=w["headers"]))["version"]
    payload = {"provider": provider, "expected_version": version}
    if provider == "mpesa":
        payload["phone"] = "0712345678"
    return client.post(
        w["pay_url"], headers={**w["headers"], "Idempotency-Key": key}, json=payload
    )


def settle(client, w, gateway, payment):
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "refunded_minor": 0,
        "transaction_reference": "pi_" + payment["id"],
    }
    return checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )


def signed_event(
    payment, *, event_id="evt_payment1", kind="checkout.session.completed"
):
    return {
        "id": event_id,
        "type": kind,
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_" + payment["id"],
                "metadata": {"payment_id": payment["id"]},
            }
        },
    }


def stripe_callback(client, event, *, age=0, secret="whsec_test"):
    body = json.dumps(event).encode()
    stamp = str(int(time.time()) - age)
    signature = hmac.new(
        secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/api/v1/payments/webhooks/stripe",
        content=body,
        headers={
            "Stripe-Signature": f"t={stamp},v1={signature}",
            "Content-Type": "application/json",
        },
    )


def test_approval_required_and_no_client_control_over_price(client, workflow, gateway):  # noqa: F811
    w = workflow
    submit(client, w)
    w["pay_url"] = w["url"] + "/payments"
    assert start(client, w).status_code == 409
    version = checked(client.get(w["url"], headers=w["headers"]))["version"]
    assert (
        client.post(
            w["pay_url"],
            headers=w["headers"],
            json={"provider": "stripe", "expected_version": version, "amount_minor": 1},
        ).status_code
        == 422
    )
    assert not gateway.starts


def test_checkout_retry_receipt_and_ledger(client, payable, gateway, db):
    w = payable
    payment = checked(start(client, w), 201)
    again = checked(start(client, w), 201)
    assert payment["id"] == again["id"] and len(gateway.starts) == 1
    assert payment["amount_minor"] == 300100 and payment["status"] == "pending"
    assert start(client, w, key="payment-create-002").status_code == 409
    assert (
        client.get(
            w["pay_url"] + f"/{payment['id']}/receipt", headers=w["headers"]
        ).status_code
        == 409
    )
    settled = settle(client, w, gateway, payment)
    assert settled["status"] == "succeeded"
    checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    receipt = checked(
        client.get(w["pay_url"] + f"/{payment['id']}/receipt", headers=w["headers"])
    )
    assert receipt["mode"] == "test" and receipt["amount_minor"] == 300100
    assert db.scalar(select(func.sum(PaymentLedger.amount_minor))) == 300100
    assert db.scalar(select(func.count()).select_from(PaymentLedger)) == 1
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"] == "paid"
    )
    assert "account_fingerprint" not in settled and "request_data" not in settled


@pytest.mark.parametrize("gate", ["disabled", "institution", "hold", "cancelled"])
def test_payment_gates(client, payable, gateway, catalog, db, monkeypatch, gate):
    w = payable
    if gate == "disabled":
        monkeypatch.setattr(settings, "PAYMENTS_ENABLED", False)
    elif gate == "institution":
        monkeypatch.setattr(settings, "PAYMENT_INSTITUTION_ID", 9999)
    elif gate == "hold":
        db.add(
            OrderHold(
                order_id=w["order"]["id"],
                category="financial",
                student_message="Clear hold",
                internal_note="Private",
                created_by=catalog["staff"].id,
            )
        )
    else:
        db.get(Order, w["order"]["id"]).status = "cancelled"
    db.commit()
    assert start(client, w).status_code in (409, 503)
    assert not gateway.starts


@pytest.mark.parametrize(
    ("secret", "age", "live"),
    [("bad", 0, False), ("whsec_test", 301, False), ("whsec_test", 0, True)],
)
def test_invalid_webhooks_cannot_mark_paid(client, payable, gateway, secret, age, live):
    payment = checked(start(client, payable), 201)
    event = signed_event(payment)
    event["livemode"] = live
    assert stripe_callback(client, event, secret=secret, age=age).status_code == 400
    assert (
        checked(client.get(payable["url"], headers=payable["headers"]))[
            "payment_status"
        ]
        == "pending"
    )


def test_webhook_queries_provider_and_deduplicates(client, payable, gateway, db):
    w = payable
    payment = checked(start(client, w), 201)
    event = signed_event(payment)
    # The signed event's name alone is not proof of payment.
    checked(stripe_callback(client, event))
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "pending"
    )
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "transaction_reference": "pi_test",
        "refunded_minor": 0,
    }
    checked(stripe_callback(client, signed_event(payment, event_id="evt_payment2")))
    checked(stripe_callback(client, signed_event(payment, event_id="evt_payment2")))
    assert db.scalar(select(func.count()).select_from(PaymentLedger)) == 1
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"] == "paid"
    )


def test_lost_checkout_response_retry_uses_same_attempt(client, payable, gateway):
    gateway.fail_start = True
    payment = checked(start(client, payable), 201)
    assert payment["status"] == "unknown"
    assert start(client, payable, key="new-payment-key").status_code == 409
    gateway.fail_start = False
    retried = checked(
        client.post(
            payable["pay_url"] + f"/{payment['id']}/retry", headers=payable["headers"]
        )
    )
    assert retried["status"] == "pending"
    assert gateway.starts == [payment["id"], payment["id"]]


def test_provider_outage_does_not_mark_failed_or_paid(client, payable, gateway):
    payment = checked(start(client, payable), 201)
    gateway.fail_observe = True
    assert (
        client.post(
            payable["pay_url"] + f"/{payment['id']}/reconcile",
            headers=payable["headers"],
        ).status_code
        == 503
    )
    assert (
        checked(client.get(payable["url"], headers=payable["headers"]))[
            "payment_status"
        ]
        == "pending"
    )


def test_expired_checkout_allows_new_attempt(client, payable, gateway):
    payment = checked(start(client, payable), 201)
    gateway.states[payment["id"]] = {"status": "expired", "refunded_minor": 0}
    checked(
        client.post(
            payable["pay_url"] + f"/{payment['id']}/reconcile",
            headers=payable["headers"],
        )
    )
    next_payment = checked(start(client, payable, key="second-checkout-key"), 201)
    assert next_payment["id"] != payment["id"]


def mpesa_payload(payment, amount=3001):
    return {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_" + payment["id"],
                "ResultCode": 0,
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": amount},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                        {"Name": "MpesaReceiptNumber", "Value": "ABC123XYZ"},
                    ]
                },
            }
        }
    }


def mpesa_callback(client, payment, payload, token=None):
    token = token if token is not None else callback_token("payments", payment["id"])
    return client.post(
        f"/api/v1/payments/webhooks/mpesa/payments/{payment['id']}?token={token}",
        json=payload,
    )


def test_mpesa_requires_authenticated_matching_callback_metadata(
    client, payable, gateway
):
    w = payable
    payment = checked(start(client, w, "mpesa"), 201)
    assert payment["phone_hint"] == "…5678"
    assert (
        mpesa_callback(
            client, payment, mpesa_payload(payment), token="wrong"
        ).status_code
        == 400
    )
    assert (
        mpesa_callback(client, payment, mpesa_payload(payment, amount=1)).status_code
        == 400
    )
    checked(mpesa_callback(client, payment, mpesa_payload(payment)))
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"] == "paid"
    )
    gateway.states[payment["id"]] = {"status": "succeeded", "refunded_minor": 0}
    checked(mpesa_callback(client, payment, mpesa_payload(payment)))
    receipt = checked(
        client.get(w["pay_url"] + f"/{payment['id']}/receipt", headers=w["headers"])
    )
    assert receipt["provider_reference"] == "ABC123XYZ"


def test_mpesa_no_rounding_or_unsafe_resends(client, payable, gateway, db):
    order = db.get(Order, payable["order"]["id"])
    snapshot = dict(order.submitted_snapshot)
    snapshot["total_minor"] = 300101
    # Test the cents gate using a fresh quote fixture's service quantity path instead of corrupting consent.
    # A submitted quote with cents is valid; update the matching consent hash for this isolated fixture.
    from app.models.orders import OrderConsent, OrderQuote
    from app.services.orders import digest

    order.submitted_snapshot = snapshot
    db.get(OrderConsent, order.submission_consent_id).scope_hash = digest(snapshot)
    db.get(OrderQuote, order.submission_quote_id).scope_hash = digest(snapshot)
    db.commit()
    assert start(client, payable, "mpesa").status_code == 422
    snapshot["total_minor"] = 300100
    order.submitted_snapshot = dict(snapshot)
    db.get(OrderConsent, order.submission_consent_id).scope_hash = digest(snapshot)
    db.get(OrderQuote, order.submission_quote_id).scope_hash = digest(snapshot)
    db.commit()
    gateway.fail_start = True
    payment = checked(start(client, payable, "mpesa"), 201)
    assert payment["status"] == "unknown"
    assert (
        client.post(
            payable["pay_url"] + f"/{payment['id']}/retry", headers=payable["headers"]
        ).status_code
        == 409
    )
    assert len(gateway.starts) == 1


def refund_body(client, w):
    return {
        "expected_version": checked(client.get(w["url"], headers=w["headers"]))[
            "version"
        ],
        "reason": "No longer need the documents",
    }


def test_refund_request_manager_approval_and_ledger(
    client, payable, gateway, catalog, auth_headers, db
):
    w = payable
    payment = checked(start(client, w), 201)
    settle(client, w, gateway, payment)
    path = f"/{payment['id']}"
    checked(
        client.post(
            w["pay_url"] + path + "/refund-requests",
            headers=w["headers"],
            json=refund_body(client, w),
        )
    )
    order = checked(client.get(w["url"], headers=w["headers"]))
    assert order["payment_status"] == "refund_requested"
    assert (
        client.post(
            w["staff_pay_url"] + path + "/refunds",
            headers=w["staff_headers"],
            json=refund_body(client, w),
        ).status_code
        == 403
    )
    result = checked(
        client.post(
            w["staff_pay_url"] + path + "/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    assert result["status"] == "refunded" and result["refund"]["status"] == "succeeded"
    assert checked(client.get(w["url"], headers=w["headers"]))["status"] == "cancelled"
    assert db.scalar(select(func.sum(PaymentLedger.amount_minor))) == 0
    assert len(gateway.refunds) == 1
    assert (
        client.post(
            w["staff_pay_url"] + path + "/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        ).status_code
        == 409
    )
    assert len(gateway.refunds) == 1


def test_pending_refund_blocks_fulfillment_until_reconciled(
    client, payable, gateway, catalog, auth_headers
):
    w = payable
    payment = checked(start(client, w), 201)
    settle(client, w, gateway, payment)
    gateway.refund_result = "pending"
    checked(
        client.post(
            w["staff_pay_url"] + f"/{payment['id']}/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    progress = checked(
        client.get(w["staff_url"] + "/fulfillment", headers=w["staff_headers"])
    )
    assert any("refund" in b for b in progress["preparation_blockers"])
    gateway.refund_result = "succeeded"
    result = checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    assert result["status"] == "refunded"


def test_partial_refund_and_dispute_block_release(client, payable, gateway):
    w = payable
    payment = checked(start(client, w), 201)
    settle(client, w, gateway, payment)
    gateway.states[payment["id"]] = {
        "status": "partially_refunded",
        "refunded_minor": 100,
    }
    checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "partially_refunded"
    )
    gateway.states[payment["id"]] = {"status": "disputed", "refunded_minor": 100}
    checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "disputed"
    )


def test_payment_and_receipt_tenant_isolation(
    client, payable, gateway, user_factory, auth_headers, catalog
):
    w = payable
    payment = checked(start(client, w), 201)
    stranger = auth_headers(user_factory("stranger-payments@example.com"))
    for suffix in ["", f"/{payment['id']}/receipt"]:
        assert client.get(w["pay_url"] + suffix, headers=stranger).status_code == 404
    assert (
        client.post(
            w["pay_url"] + f"/{payment['id']}/reconcile", headers=stranger
        ).status_code
        == 404
    )
    assert client.get(w["staff_pay_url"], headers=stranger).status_code == 403
    assert (
        client.get(
            f"/api/v1/staff/institutions/{catalog['institution'].id}/payment-reconciliation",
            headers=stranger,
        ).status_code
        == 403
    )


def test_concurrent_creation_uses_one_provider_request(
    client, payable, gateway, engine, db
):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL lock check")
    w = payable
    version = checked(client.get(w["url"], headers=w["headers"]))["version"]
    headers = {**w["headers"], "Idempotency-Key": "concurrent-payment-key"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    w["pay_url"],
                    headers=headers,
                    json={"provider": "stripe", "expected_version": version},
                ),
                range(2),
            )
        )
    assert [r.status_code for r in responses] == [201, 201]
    assert len(gateway.starts) == 1
    assert db.scalar(select(func.count()).select_from(PaymentAttempt)) == 1


def test_stripe_adapter_checks_amount_currency_and_metadata(
    client, payable, gateway, db, monkeypatch
):
    payment = checked(start(client, payable), 201)
    model = db.get(PaymentAttempt, payment["id"])
    adapter = Gateways()
    data = {
        "id": model.provider_reference,
        "client_reference_id": model.id,
        "metadata": {"payment_id": model.id},
        "amount_total": model.amount_minor,
        "currency": "kes",
        "livemode": False,
        "payment_status": "paid",
        "payment_intent": {
            "id": "pi_test",
            "status": "succeeded",
            "amount_received": model.amount_minor,
            "currency": "kes",
            "metadata": {"payment_id": model.id},
            "latest_charge": {
                "payment_intent": "pi_test",
                "amount": model.amount_minor,
                "paid": True,
                "captured": True,
                "amount_refunded": 0,
            },
        },
    }
    monkeypatch.setattr(adapter, "stripe", lambda *args, **kwargs: data)
    assert adapter.observe(model)["status"] == "succeeded"
    for key, bad in [
        ("amount_total", 1),
        ("currency", "usd"),
        ("metadata", {}),
        ("livemode", True),
    ]:
        previous = data[key]
        data[key] = bad
        with pytest.raises(GatewayUnavailable):
            adapter.observe(model)
        data[key] = previous


def test_disabling_new_collection_still_allows_confirmation(
    client, payable, gateway, monkeypatch
):
    payment = checked(start(client, payable), 201)
    monkeypatch.setattr(settings, "PAYMENTS_ENABLED", False)
    assert settle(client, payable, gateway, payment)["status"] == "succeeded"


def test_lost_response_recovered_by_signed_checkout_event(client, payable, gateway, db):
    gateway.fail_start = True
    payment = checked(start(client, payable), 201)
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "refunded_minor": 0,
        "transaction_reference": "pi_recovered",
    }
    checked(stripe_callback(client, signed_event(payment)))
    assert (
        db.get(PaymentAttempt, payment["id"]).provider_reference
        == "cs_" + payment["id"]
    )
    assert (
        checked(client.get(payable["url"], headers=payable["headers"]))[
            "payment_status"
        ]
        == "paid"
    )


def test_old_uncertain_stripe_request_is_not_resent(client, payable, gateway, db):
    from datetime import timedelta

    gateway.fail_start = True
    payment = checked(start(client, payable), 201)
    model = db.get(PaymentAttempt, payment["id"])
    model.created_at = utcnow() - timedelta(hours=24)
    db.commit()
    assert (
        client.post(
            payable["pay_url"] + f"/{payment['id']}/retry", headers=payable["headers"]
        ).status_code
        == 409
    )
    assert len(gateway.starts) == 1


def test_refund_rejection_and_unknown_refund_retry(
    client, payable, gateway, catalog, auth_headers, db
):
    w = payable
    payment = checked(start(client, w), 201)
    settle(client, w, gateway, payment)
    path = f"/{payment['id']}"
    checked(
        client.post(
            w["pay_url"] + path + "/refund-requests",
            headers=w["headers"],
            json=refund_body(client, w),
        )
    )
    checked(
        client.post(
            w["staff_pay_url"] + path + "/refund-rejections",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"] == "paid"
    )
    gateway.fail_refund = True
    result = checked(
        client.post(
            w["staff_pay_url"] + path + "/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    assert result["refund"]["status"] == "unknown"
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "refund_pending"
    )
    gateway.fail_refund = False
    checked(
        client.post(
            w["staff_pay_url"] + path + "/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    assert gateway.refunds[0] == gateway.refunds[1]
    assert db.scalar(select(func.count()).select_from(PaymentRefund)) == 1


def test_mpesa_reversal_authentication_correlation_and_replay(
    client, payable, gateway, catalog, auth_headers, db
):
    w = payable
    payment = checked(start(client, w, "mpesa"), 201)
    gateway.states[payment["id"]] = {"status": "succeeded", "refunded_minor": 0}
    checked(mpesa_callback(client, payment, mpesa_payload(payment)))
    gateway.refund_result = "pending"
    refunded = checked(
        client.post(
            w["staff_pay_url"] + f"/{payment['id']}/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    identifier = refunded["refund"]["id"]
    url = f"/api/v1/payments/webhooks/mpesa/reversals/{identifier}"
    payload = {
        "Result": {
            "ConversationID": "re_" + identifier,
            "OriginatorConversationID": "cor_" + identifier,
            "ResultCode": 0,
        }
    }
    assert client.post(url + "?token=bad", json=payload).status_code == 400
    token = callback_token("reversals", identifier)
    bad = {"Result": {**payload["Result"], "ConversationID": "foreign"}}
    assert client.post(url + "?token=" + token, json=bad).status_code == 409
    checked(client.post(url + "?token=" + token, json=payload))
    checked(client.post(url + "?token=" + token, json=payload))
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "refunded"
    )
    assert db.scalar(select(func.sum(PaymentLedger.amount_minor))) == 0


def test_mpesa_reversal_callback_can_arrive_before_acknowledgment(
    client, payable, gateway, catalog, auth_headers
):
    w = payable
    payment = checked(start(client, w, "mpesa"), 201)
    gateway.states[payment["id"]] = {"status": "succeeded", "refunded_minor": 0}
    checked(mpesa_callback(client, payment, mpesa_payload(payment)))

    def early_callback(payment, refund):
        token = callback_token("reversals", refund.id)
        payload = {
            "Result": {
                "ConversationID": "early_ref",
                "OriginatorConversationID": "early_cor",
                "ResultCode": 0,
            }
        }
        checked(
            client.post(
                f"/api/v1/payments/webhooks/mpesa/reversals/{refund.id}?token={token}",
                json=payload,
            )
        )
        return {
            "reference": "early_ref",
            "correlation_id": "early_cor",
            "status": "pending",
        }

    gateway.refund = early_callback
    result = checked(
        client.post(
            w["staff_pay_url"] + f"/{payment['id']}/refunds",
            headers=auth_headers(catalog["manager"]),
            json=refund_body(client, w),
        )
    )
    assert result["status"] == "refunded"


def test_worker_reconciles_confirmed_callback_receipt_without_resending(
    client, payable, gateway, engine, monkeypatch
):
    from app import payment_worker

    w = payable
    payment = checked(start(client, w, "mpesa"), 201)
    checked(mpesa_callback(client, payment, mpesa_payload(payment)))
    gateway.states[payment["id"]] = {"status": "succeeded", "refunded_minor": 0}
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        db.get(PaymentAttempt, payment["id"]).checked_at = None
        db.commit()
    monkeypatch.setattr(payment_worker, "engine", engine)
    monkeypatch.setattr(payment_worker, "get_gateways", lambda: gateway)
    assert payment_worker.run() == 0
    receipt = checked(
        client.get(w["pay_url"] + f"/{payment['id']}/receipt", headers=w["headers"])
    )
    assert receipt["provider_reference"] == "ABC123XYZ"
    assert len(gateway.starts) == 1


def test_paid_state_does_not_regress_on_delayed_failure(client, payable, gateway):
    payment = checked(start(client, payable), 201)
    settle(client, payable, gateway, payment)
    gateway.states[payment["id"]] = {"status": "failed", "refunded_minor": 0}
    checked(
        client.post(
            payable["pay_url"] + f"/{payment['id']}/reconcile",
            headers=payable["headers"],
        )
    )
    assert (
        checked(client.get(payable["url"], headers=payable["headers"]))[
            "payment_status"
        ]
        == "paid"
    )


def test_concurrent_webhook_delivery_books_one_charge(
    client, payable, gateway, engine, db
):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL locking check")
    payment = checked(start(client, payable), 201)
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "refunded_minor": 0,
        "transaction_reference": "pi_concurrent",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: stripe_callback(client, signed_event(payment)), range(2))
        )
    assert [r.status_code for r in responses] == [200, 200]
    assert db.scalar(select(func.count()).select_from(PaymentLedger)) == 1


def test_real_adapter_builds_provider_requests_without_rounding(
    client, payable, gateway, db, monkeypatch
):
    payment = checked(start(client, payable), 201)
    model = db.get(PaymentAttempt, payment["id"])
    calls = []
    adapter = Gateways()

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {
            "id": model.provider_reference,
            "url": "https://checkout.stripe.com/c/test",
        }

    monkeypatch.setattr(adapter, "request", request)
    adapter.initiate(model)
    _, url, kwargs = calls[0]
    assert url == "https://api.stripe.com/v1/checkout/sessions"
    assert kwargs["headers"]["Idempotency-Key"] == "checkout-" + model.id
    assert kwargs["data"]["line_items[0][price_data][unit_amount]"] == "300100"
    assert kwargs["data"]["metadata[payment_id]"] == model.id


@pytest.mark.parametrize(
    "code,status",
    [
        (1037, "failed"),
        (1032, "failed"),
        (1025, "failed"),
        (9999, "failed"),
        (1019, "expired"),
        (4999, "pending"),
        (1001, "pending"),
        (None, "pending"),
    ],
)
def test_mpesa_adapter_queries_exact_request_and_maps_final_results(
    client, payable, gateway, db, monkeypatch, code, status
):
    payment = checked(start(client, payable, "mpesa"), 201)
    model = db.get(PaymentAttempt, payment["id"])
    adapter = Gateways()
    calls = []

    def mpesa(path, payload):
        calls.append((path, payload))
        return {"CheckoutRequestID": model.provider_reference, "ResultCode": code}

    monkeypatch.setattr(adapter, "mpesa", mpesa)
    assert adapter.observe(model)["status"] == status
    assert calls[0][1]["CheckoutRequestID"] == model.provider_reference


def test_webhook_body_limit_and_invalid_json(client, gateway):
    assert (
        client.post(
            "/api/v1/payments/webhooks/stripe", content=b"x" * 65537
        ).status_code
        == 413
    )
    assert (
        client.post("/api/v1/payments/webhooks/stripe", content=b"{}").status_code
        == 400
    )


def test_callback_tokens_are_removed_from_access_logs():
    import logging

    from app.services.payment_gateways import PaymentAccessLogFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "POST",
            "/api/v1/payments/webhooks/mpesa/payments/id?token=do-not-log",
            "1.1",
            200,
        ),
        None,
    )
    assert PaymentAccessLogFilter().filter(record)
    assert (
        "do-not-log" not in record.getMessage() and "?token=" not in record.getMessage()
    )


def test_payment_configuration_rejects_weak_secret_and_wrong_mode():
    from pydantic import ValidationError

    from app.core.config import Settings

    base = {
        "_env_file": None,
        "DATABASE_URL": "sqlite://",
        "APP_ENV": "test",
        "PAYMENTS_ENABLED": True,
        "PAYMENT_INSTITUTION_ID": 1,
        "SECRET_KEY": "a-test-secret-at-least-thirty-two-characters",
    }
    with pytest.raises(ValidationError):
        Settings(**{**base, "SECRET_KEY": "short"})
    with pytest.raises(ValidationError):
        Settings(
            **base, PAYMENT_MODE="test", STRIPE_SECRET_KEY="sk_live_not-a-real-key"
        )
    with pytest.raises(ValidationError):
        Settings(
            **base, PAYMENT_MODE="live", PAYMENT_PUBLIC_URL="http://localhost:8000"
        )


def test_manager_cannot_refund_own_order(
    client, payable, gateway, catalog, auth_headers, db
):
    from app.models.access import MembershipRole
    from tests.fixtures_academic import grant

    w = payable
    payment = checked(start(client, w), 201)
    settle(client, w, gateway, payment)
    grant(db, w["user"], catalog["institution"], MembershipRole.MANAGER)
    assert (
        client.post(
            w["staff_pay_url"] + f"/{payment['id']}/refunds",
            headers=auth_headers(w["user"]),
            json=refund_body(client, w),
        ).status_code
        == 403
    )
    assert not gateway.refunds


def test_late_second_success_is_flagged_for_reconciliation(client, payable, gateway):
    w = payable
    first = checked(start(client, w), 201)
    gateway.states[first["id"]] = {"status": "expired", "refunded_minor": 0}
    checked(
        client.post(w["pay_url"] + f"/{first['id']}/reconcile", headers=w["headers"])
    )
    second = checked(start(client, w, key="another-unique-key"), 201)
    settle(client, w, gateway, first)
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "review_required"
    )
    settle(client, w, gateway, second)
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "review_required"
    )


def test_external_refund_totals_are_booked_once(client, payable, gateway, db):
    w = payable
    payment = checked(start(client, w), 201)
    settle(client, w, gateway, payment)
    for amount in (100, 100, 300100, 300100):
        gateway.states[payment["id"]] = {
            "status": "refunded" if amount == 300100 else "partially_refunded",
            "refunded_minor": amount,
        }
        checked(
            client.post(
                w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"]
            )
        )
    assert db.scalar(select(func.sum(PaymentLedger.amount_minor))) == 0
    assert db.scalar(select(func.count()).select_from(PaymentLedger)) == 3


def test_rechecking_old_expired_attempt_does_not_clear_new_payment(
    client, payable, gateway
):
    w = payable
    first = checked(start(client, w), 201)
    gateway.states[first["id"]] = {"status": "expired", "refunded_minor": 0}
    checked(
        client.post(w["pay_url"] + f"/{first['id']}/reconcile", headers=w["headers"])
    )
    second = checked(start(client, w, key="replacement-payment-key"), 201)
    checked(
        client.post(w["pay_url"] + f"/{first['id']}/reconcile", headers=w["headers"])
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"]
        == "pending"
    )
    settle(client, w, gateway, second)
    checked(
        client.post(w["pay_url"] + f"/{first['id']}/reconcile", headers=w["headers"])
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["payment_status"] == "paid"
    )


@pytest.mark.parametrize("status,code", [(404, "404.001.03"), (400, "400.003.01")])
def test_mpesa_explicit_invalid_token_is_rejected(monkeypatch, status, code):
    import httpx

    from app.services.payment_gateways import GatewayAuthenticationRejected

    original_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status, json={"errorCode": code, "errorMessage": "Invalid Access Token"}
        )
    )
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
    )
    with pytest.raises(GatewayAuthenticationRejected):
        Gateways().request(
            "POST",
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            json={},
        )
    # An unrecognized refusal remains uncertain instead of allowing a blind retry.
    with pytest.raises(GatewayUnavailable):
        Gateways().request(
            "POST",
            "https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query",
            json={},
        )


def test_mpesa_authorization_failure_allows_retry_without_releasing_order(
    client, payable, gateway
):
    from app.services.payment_gateways import GatewayAuthenticationRejected

    original = gateway.initiate

    def reject(payment):
        raise GatewayAuthenticationRejected

    gateway.initiate = reject
    first = checked(start(client, payable, "mpesa"), 201)
    assert first["status"] == "failed"
    assert "Daraja credentials" in first["failure_reason"]
    assert (
        checked(client.get(payable["pay_url"], headers=payable["headers"]))[
            "payment_status"
        ]
        == "not_started"
    )
    gateway.initiate = original
    second = checked(
        start(client, payable, "mpesa", key="retry-after-auth-refusal"), 201
    )
    assert second["id"] != first["id"] and second["status"] == "pending"


@pytest.mark.parametrize("outcome", ["failed", "expired"])
def test_retry_prompt_checks_previous_attempt_and_replays_one_new_prompt(
    client, payable, gateway, outcome
):
    previous = checked(start(client, payable, provider="mpesa"), 201)
    gateway.states[previous["id"]] = {"status": outcome, "refunded_minor": 0}
    url = payable["pay_url"] + f"/{previous['id']}/retry-prompt"
    headers = {**payable["headers"], "Idempotency-Key": "retry-checked-prompt"}
    first = checked(client.post(url, headers=headers))
    replay = checked(client.post(url, headers=headers))
    assert first["id"] != previous["id"]
    assert first["id"] == replay["id"]
    assert gateway.starts == [previous["id"], first["id"]]


@pytest.mark.parametrize("unavailable", [False, True])
def test_retry_prompt_does_not_resend_pending_or_unverifiable_payment(
    client, payable, gateway, unavailable
):
    gateway.fail_start = unavailable
    previous = checked(start(client, payable, provider="mpesa"), 201)
    response = client.post(
        payable["pay_url"] + f"/{previous['id']}/retry-prompt",
        headers={**payable["headers"], "Idempotency-Key": "retry-unconfirmed-prompt"},
    )
    assert response.status_code == 409
    assert gateway.starts == [previous["id"]]


def test_retry_prompt_detects_success_without_sending_another_prompt(
    client, payable, gateway
):
    previous = checked(start(client, payable, provider="mpesa"), 201)
    gateway.states[previous["id"]] = {
        "status": "succeeded",
        "refunded_minor": 0,
        "transaction_reference": "RETRYSUCCESS",
    }
    result = checked(
        client.post(
            payable["pay_url"] + f"/{previous['id']}/retry-prompt",
            headers={**payable["headers"], "Idempotency-Key": "retry-paid-prompt"},
        )
    )
    assert result["status"] == "succeeded"
    assert gateway.starts == [previous["id"]]


def test_mpesa_oauth_failure_is_safe_to_retry_before_stk_dispatch(monkeypatch):
    from app.services.payment_gateways import GatewayAuthenticationRejected, Gateways

    calls = []

    def request(self, method, url, **kwargs):
        calls.append((method, url))
        raise GatewayUnavailable

    monkeypatch.setattr(Gateways, "request", request)
    with pytest.raises(GatewayAuthenticationRejected):
        Gateways().mpesa("/mpesa/stkpush/v1/processrequest", {})
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    with pytest.raises(GatewayUnavailable):
        Gateways().mpesa("/mpesa/stkpushquery/v1/query", {})


def test_stale_sandbox_request_can_be_retired_and_retried_once(
    client, payable, gateway, db
):
    from datetime import timedelta

    from app.models.payments import PaymentEvent

    gateway.fail_start = True
    previous = checked(start(client, payable, "mpesa"), 201)
    model = db.get(PaymentAttempt, previous["id"])
    model.created_at = utcnow() - timedelta(minutes=3)
    db.commit()
    gateway.fail_start = False
    headers = {**payable["headers"], "Idempotency-Key": "retire-sandbox-and-retry"}
    url = payable["pay_url"] + f"/{previous['id']}/retry-prompt"
    fresh = checked(client.post(url, headers=headers))
    replay = checked(client.post(url, headers=headers))
    db.refresh(model)
    assert model.status == "expired"
    assert model.active_order_id is None
    assert fresh["id"] == replay["id"] != previous["id"]
    assert gateway.starts == [previous["id"], fresh["id"]]
    assert (
        db.scalar(
            select(PaymentEvent.id).where(
                PaymentEvent.payment_id == model.id,
                PaymentEvent.kind == "payment_test_expired",
            )
        )
        is not None
    )
    assert (
        db.scalar(select(PaymentLedger.id).where(PaymentLedger.payment_id == model.id))
        is None
    )


@pytest.mark.parametrize("guard", ["live", "accepted", "ledger", "paid", "refund"])
def test_sandbox_clock_cannot_retire_real_accepted_or_funded_requests(
    client, payable, gateway, db, monkeypatch, guard
):
    from datetime import timedelta

    from app.services.payments import expire_test_request

    gateway.fail_start = True
    previous = checked(start(client, payable, "mpesa"), 201)
    model = db.get(PaymentAttempt, previous["id"])
    model.created_at = utcnow() - timedelta(days=1)
    if guard == "live":
        model.mode = "live"
        monkeypatch.setattr(settings, "PAYMENT_MODE", "live")
    elif guard == "accepted":
        model.provider_reference = "ws_accepted"
    elif guard == "ledger":
        db.add(
            PaymentLedger(
                payment_id=model.id, entry_key="charge", amount_minor=model.amount_minor
            )
        )
    elif guard == "paid":
        model.paid_at = utcnow()
    else:
        db.add(
            PaymentRefund(
                payment_id=model.id,
                order_id=model.order_id,
                requested_by=payable["user"].id,
                reason="Review",
                amount_minor=model.amount_minor,
            )
        )
    db.commit()
    assert not expire_test_request(db, db.get(Order, model.order_id), model)
    assert model.status == "unknown"


def test_final_failure_callback_unlocks_retry_even_when_query_is_unavailable(
    client, payable, gateway
):
    previous = checked(start(client, payable, "mpesa"), 201)
    gateway.fail_observe = True
    payload = {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_" + previous["id"],
                "ResultCode": 1032,
                "ResultDesc": "Cancelled",
            }
        }
    }
    checked(mpesa_callback(client, previous, payload))
    attempts = checked(client.get(payable["pay_url"], headers=payable["headers"]))[
        "attempts"
    ]
    assert attempts[-1]["status"] == "failed"
    assert "cancelled" in attempts[-1]["failure_reason"]
    assert (
        checked(start(client, payable, "mpesa", key="after-cancel"), 201)["status"]
        == "pending"
    )


def test_failure_callback_cannot_overwrite_confirmed_payment(client, payable, gateway):
    previous = checked(start(client, payable, "mpesa"), 201)
    settle(client, payable, gateway, previous)
    payload = {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_" + previous["id"],
                "ResultCode": 1032,
            }
        }
    }
    checked(mpesa_callback(client, previous, payload))
    attempts = checked(client.get(payable["pay_url"], headers=payable["headers"]))[
        "attempts"
    ]
    assert attempts[-1]["status"] == "succeeded"


def test_worker_retires_missing_reference_sandbox_attempt_without_new_prompt(
    client, payable, gateway, db, engine, monkeypatch
):
    from datetime import timedelta

    from app import payment_worker

    monkeypatch.setattr(payment_worker, "engine", engine)
    monkeypatch.setattr(payment_worker, "get_gateways", lambda: gateway)
    gateway.fail_start = True
    previous = checked(start(client, payable, "mpesa"), 201)
    model = db.get(PaymentAttempt, previous["id"])
    model.created_at = utcnow() - timedelta(minutes=3)
    model.account_fingerprint = "retired-test-credentials"
    db.commit()
    payment_worker.run()
    db.refresh(model)
    assert model.status == "expired"
    assert gateway.starts == [previous["id"]]


def test_delayed_live_request_is_queued_once_and_is_not_recharged(
    client, payable, gateway, db, monkeypatch
):
    from datetime import timedelta

    from app.models.payments import PaymentEvent

    gateway.fail_start = True
    previous = checked(start(client, payable, "mpesa"), 201)
    model = db.get(PaymentAttempt, previous["id"])
    model.mode = "live"
    model.created_at = utcnow() - timedelta(minutes=3)
    db.commit()
    monkeypatch.setattr(settings, "PAYMENT_MODE", "live")
    url = payable["pay_url"] + f"/{previous['id']}/reconcile"
    for _ in range(2):
        result = checked(client.post(url, headers=payable["headers"]))
        assert result["status"] == "unknown"
        assert result["confirmation_delayed"] is True
        assert result["can_retry_prompt"] is False
    assert (
        db.scalar(
            select(func.count())
            .select_from(PaymentEvent)
            .where(
                PaymentEvent.payment_id == model.id,
                PaymentEvent.kind == "payment_recovery_required",
            )
        )
        == 1
    )
    assert gateway.starts == [previous["id"]]


@pytest.mark.parametrize(
    "kind",
    ["ConnectError", "ConnectTimeout", "PoolTimeout", "ReadTimeout", "WriteTimeout"],
)
def test_stk_connection_failures_can_retry_but_lost_responses_remain_uncertain(
    monkeypatch, kind
):
    import httpx

    from app.services.payment_gateways import GatewayNotDispatched

    original_client = httpx.Client

    def fail(request):
        raise getattr(httpx, kind)("test transport failure", request=request)

    transport = httpx.MockTransport(fail)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
    )
    expected = (
        GatewayNotDispatched
        if kind in ("ConnectError", "ConnectTimeout", "PoolTimeout")
        else GatewayUnavailable
    )
    with pytest.raises(expected):
        Gateways().request(
            "POST",
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            json={},
        )


def test_success_callback_settles_once_without_calling_a_broken_query(
    client, payable, gateway, db
):
    previous = checked(start(client, payable, "mpesa"), 201)

    def broken_query(payment):
        raise AssertionError("A valid success callback must not need a status query")

    gateway.observe = broken_query
    payload = mpesa_payload(previous)
    checked(mpesa_callback(client, previous, payload))
    checked(mpesa_callback(client, previous, payload))
    result = checked(client.get(payable["url"], headers=payable["headers"]))
    assert result["payment_status"] == "paid"
    assert (
        checked(
            client.post(
                payable["pay_url"] + f"/{previous['id']}/reconcile",
                headers=payable["headers"],
            )
        )["status"]
        == "succeeded"
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(PaymentLedger)
            .where(
                PaymentLedger.payment_id == previous["id"],
                PaymentLedger.entry_key == "charge",
            )
        )
        == 1
    )
    assert db.get(PaymentAttempt, previous["id"]).transaction_reference == "ABC123XYZ"


def test_saved_validated_success_is_replayed_when_status_query_is_down(
    client, payable, gateway, db
):
    from app.models.payments import PaymentWebhook

    previous = checked(start(client, payable, "mpesa"), 201)
    # The previous handler validated and persisted this receipt before its query failed.
    db.add(
        PaymentWebhook(
            provider="mpesa",
            event_key="stored-valid-success",
            payment_id=previous["id"],
            payload={
                "reference": "ws_" + previous["id"],
                "receipt": "RECOVERSUCCESS",
                "result_code": "0",
                "amount_minor": previous["amount_minor"],
                "phone_matches": True,
            },
        )
    )
    db.commit()
    gateway.fail_observe = True
    result = checked(
        client.post(
            payable["pay_url"] + f"/{previous['id']}/reconcile",
            headers=payable["headers"],
        )
    )
    assert result["status"] == "succeeded"
    assert (
        checked(client.get(payable["url"], headers=payable["headers"]))[
            "payment_status"
        ]
        == "paid"
    )
    assert gateway.starts == [previous["id"]]
