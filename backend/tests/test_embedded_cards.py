"""Secure card fields never send card numbers or CVC through application endpoints."""
# ruff: noqa: F811 -- shared pytest fixtures

import pytest

from app.core.config import settings
from app.models.payments import PaymentAttempt
from app.services.payment_gateways import Gateways, GatewayUnavailable
from tests.test_orders import checked, submit
from tests.test_payments import gateway, start  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401


@pytest.fixture
def embedded(client, upfront, gateway, monkeypatch, db):
    monkeypatch.setattr(settings, "STRIPE_PUBLISHABLE_KEY", "pk_test_example")
    gateway.card_checkout = lambda payment: (
        payment.provider_reference + "_secret_example"
    )
    submit(client, upfront)
    payment = checked(start(client, upfront), 201)
    return upfront, payment


def test_embedded_card_bootstrap_is_owner_only_and_not_cached(
    client, embedded, user_factory, auth_headers, db
):
    w, payment = embedded
    attempt = db.get(PaymentAttempt, payment["id"])
    assert attempt.request_data["ui_mode"] == "embedded"
    assert (
        "success_url" not in attempt.request_data
        and "cancel_url" not in attempt.request_data
    )
    assert "client_secret" not in attempt.request_data
    path = w["pay_url"] + f"/{payment['id']}/card-checkout"
    response = client.get(path, headers=w["headers"])
    assert (
        response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    )
    assert response.json()["publishable_key"] == "pk_test_example"
    assert (
        client.get(
            path, headers=auth_headers(user_factory("other-card@example.com"))
        ).status_code
        == 404
    )
    assert client.get(path).status_code == 401
    assert "client_secret" not in str(
        checked(client.get(w["pay_url"], headers=w["headers"]))
    )
    assert checked(client.get(w["url"], headers=w["headers"]))["submitted_at"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_total", 1),
        ("status", "complete"),
        ("client_reference_id", "other"),
        ("client_secret", "wrong_secret"),
        ("livemode", True),
        ("metadata", None),
    ],
)
def test_card_session_must_match_original_payment(
    embedded, db, monkeypatch, field, value
):
    _, payment = embedded
    attempt = db.get(PaymentAttempt, payment["id"])
    data = {
        "id": attempt.provider_reference,
        "client_reference_id": attempt.id,
        "metadata": {"payment_id": attempt.id},
        "amount_total": attempt.amount_minor,
        "currency": "kes",
        "livemode": False,
        "status": "open",
        "ui_mode": "embedded",
        "client_secret": attempt.provider_reference + "_secret_example",
    }
    gateway = Gateways()
    monkeypatch.setattr(gateway, "stripe", lambda *a, **k: {**data, field: value})
    with pytest.raises(GatewayUnavailable):
        gateway.card_checkout(attempt)


def test_closed_checkout_cannot_reload_fields(client, embedded, db):
    w, payment = embedded
    attempt = db.get(PaymentAttempt, payment["id"])
    attempt.status = "succeeded"
    db.commit()
    assert (
        client.get(
            w["pay_url"] + f"/{payment['id']}/card-checkout", headers=w["headers"]
        ).status_code
        == 409
    )


def test_adapter_opens_embedded_fields_without_hosted_url(embedded, db, monkeypatch):
    _, payment = embedded
    attempt = db.get(PaymentAttempt, payment["id"])
    gateway = Gateways()
    data = {
        "id": attempt.provider_reference,
        "client_reference_id": attempt.id,
        "metadata": {"payment_id": attempt.id},
        "amount_total": attempt.amount_minor,
        "currency": "kes",
        "livemode": False,
        "status": "open",
        "ui_mode": "embedded",
        "client_secret": attempt.provider_reference + "_secret_example",
        "url": None,
    }
    monkeypatch.setattr(gateway, "stripe", lambda *a, **k: data)
    assert gateway.initiate(attempt) == {
        "reference": attempt.provider_reference,
        "checkout_url": None,
    }
    assert gateway.card_checkout(attempt) == data["client_secret"]
