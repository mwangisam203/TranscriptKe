"""Only unfinished, unpaid drafts can be removed; committed orders are retained."""
# ruff: noqa: F811 -- shared pytest fixtures

from sqlalchemy import func, select

from app.models.access import AccessEvent
from app.models.orders import Order, OrderConsent, OrderEvent, OrderQuote
from tests.test_orders import authorize, checked, submit, workflow  # noqa: F401
from tests.test_payments import gateway, start  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401


def test_delete_draft_removes_private_data_and_retains_deletion_audit(
    client, workflow, db
):
    w = workflow
    authorize(client, w)
    version = checked(client.get(w["url"], headers=w["headers"]))["version"]
    assert (
        client.delete(
            w["url"] + f"?expected_version={version - 1}", headers=w["headers"]
        ).status_code
        == 409
    )
    response = client.delete(
        w["url"] + f"?expected_version={version}", headers=w["headers"]
    )
    assert response.status_code == 204, response.text
    assert client.get(w["url"], headers=w["headers"]).status_code == 404
    for model in (Order, OrderQuote, OrderConsent, OrderEvent):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert (
        db.scalar(
            select(AccessEvent).where(AccessEvent.action == "unfinished_order_deleted")
        )
        is not None
    )


def test_other_account_cannot_delete_draft(
    client, workflow, user_factory, auth_headers
):
    assert (
        client.delete(
            workflow["url"] + f"?expected_version={workflow['order']['version']}",
            headers=auth_headers(user_factory("other-delete@example.com")),
        ).status_code
        == 404
    )
    assert client.get(workflow["url"], headers=workflow["headers"]).status_code == 200


def test_pending_and_successful_checkouts_are_permanent(client, upfront, gateway):
    w = upfront
    order, _, _ = submit(client, w)
    assert (
        client.delete(
            w["url"] + f"?expected_version={order['version']}", headers=w["headers"]
        ).status_code
        == 409
    )
    payment = checked(start(client, w), 201)
    order = checked(client.get(w["url"], headers=w["headers"]))
    assert (
        client.delete(
            w["url"] + f"?expected_version={order['version']}", headers=w["headers"]
        ).status_code
        == 409
    )
    gateway.states[payment["id"]] = {
        "status": "succeeded",
        "transaction_reference": "pi_retained",
        "refunded_minor": 0,
    }
    checked(
        client.post(w["pay_url"] + f"/{payment['id']}/reconcile", headers=w["headers"])
    )
    order = checked(client.get(w["url"], headers=w["headers"]))
    assert order["status"] == "submitted"
    assert (
        client.delete(
            w["url"] + f"?expected_version={order['version']}", headers=w["headers"]
        ).status_code
        == 409
    )
    assert len(checked(client.get(w["pay_url"], headers=w["headers"]))["attempts"]) == 1
