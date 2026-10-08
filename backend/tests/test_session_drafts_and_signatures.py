"""Refresh recovery, private drafts and quote-bound drawn consent."""
# ruff: noqa: F811

import json

import pytest
from sqlalchemy import select

from app.models.orders import OrderConsent
from app.models.workspace_draft import WorkspaceDraft
from tests.conftest import PASSWORD
from tests.test_academic_records import ME, submission
from tests.test_orders import authorize, checked, workflow  # noqa: F401


def test_cookie_restores_existing_session_and_logout_revokes(client, user_factory):
    user = user_factory()
    login = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert login.status_code == 200
    cookie = login.headers["set-cookie"]
    assert (
        "HttpOnly" in cookie
        and "SameSite=strict" in cookie
        and "Path=/api/v1" in cookie
    )
    original = login.json()["access_token"]
    restored = client.get("/api/v1/auth/session")
    assert restored.json()["access_token"] == original
    assert restored.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/auth/me").json()["id"] == user.id
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Origin": "http://testserver"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/session").status_code == 401
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer " + original}
        ).status_code
        == 401
    )


def test_login_rejects_cross_origin_and_password_change_invalidates_cookie(
    client, user_factory, auth_headers
):
    user = user_factory()
    credentials = {"email": user.email, "password": PASSWORD}
    assert (
        client.post(
            "/api/v1/auth/login",
            json=credentials,
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )
    client.post("/api/v1/auth/login", json=credentials)
    assert (
        client.put(
            "/api/v1/auth/password",
            headers=auth_headers(user),
            json={"current_password": PASSWORD, "password": "new password 123"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/session").status_code == 401


def test_recovery_drafts_are_encrypted_private_and_versioned(
    client, user_factory, auth_headers, db
):
    owner = user_factory()
    other = user_factory("other@example.com")
    url = "/api/v1/me/workspace-drafts/enrollment"
    headers = auth_headers(owner)
    data = {
        "name_on_record": "Private Draft Name",
        "id_number": "01234567",
        "attendance_start_date": "2020-",
    }
    saved = client.put(url, headers=headers, json={"expected_version": 0, "data": data})
    assert saved.status_code == 200 and saved.json()["version"] == 1
    row = db.scalar(select(WorkspaceDraft))
    assert (
        "Private Draft Name" not in row.details_ciphertext
        and "01234567" not in row.details_ciphertext
    )
    assert client.get(url, headers=headers).json()["data"] == data
    assert client.get(url, headers=auth_headers(other)).json() == {
        "version": 0,
        "data": {},
    }
    assert client.get(url).status_code == 401
    assert (
        client.put(
            url, headers=headers, json={"expected_version": 0, "data": {}}
        ).status_code
        == 409
    )
    assert (
        client.put(
            url, headers=headers, json={"expected_version": 1, "data": {}}
        ).json()["version"]
        == 2
    )
    assert client.get(url, headers=headers).headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "data",
    [
        {"password": "secret password"},
        {"nested": {"token": "secret"}},
        {"signature": []},
        {"field": "a" * 33000},
    ],
)
def test_recovery_rejects_secrets_and_large_payloads(
    client, user_factory, auth_headers, data
):
    response = client.put(
        "/api/v1/me/workspace-drafts/order-start",
        headers=auth_headers(user_factory()),
        json={"expected_version": 0, "data": data},
    )
    assert response.status_code == 422
    assert "secret password" not in response.text


def test_recovery_order_ownership_and_submitted_state(
    client, workflow, user_factory, auth_headers
):
    w = workflow
    url = f"/api/v1/me/workspace-drafts/order-{w['order']['id']}"
    other = auth_headers(user_factory("other@example.com"))
    assert (
        client.put(
            url, headers=other, json={"expected_version": 0, "data": {}}
        ).status_code
        == 404
    )
    assert client.get(url, headers=other).status_code == 404
    quote, payload = authorize(client, w)
    checked(
        client.post(
            w["url"] + "/submit",
            headers={**w["headers"], "Idempotency-Key": "signed-draft-submit"},
            json=payload,
        )
    )
    assert (
        client.put(
            url, headers=w["headers"], json={"expected_version": 0, "data": {}}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "number,kind,valid",
    [
        ("1234567", "national_id", True),
        ("01234567", "national_id", True),
        ("123456", "national_id", False),
        ("123456789", "national_id", False),
        ("AB123456", "national_id", False),
        ("AB123456", "passport", True),
    ],
)
def test_national_id_length_with_separate_passport_rule(
    client, catalog, user_factory, auth_headers, number, kind, valid
):
    response = client.post(
        ME,
        headers=auth_headers(user_factory()),
        json=submission(
            catalog, admission_number=None, id_number=number, id_number_type=kind
        ),
    )
    assert response.status_code == (201 if valid else 422)
    if valid:
        assert response.json()["id_number_type"] == kind
    else:
        assert number not in response.text


def test_signed_consent_encrypted_scoped_and_immutable(
    client, workflow, user_factory, auth_headers, db
):
    w = workflow
    quote, payload = authorize(client, w)
    consent_id = payload["consent_id"]
    row = db.get(OrderConsent, consent_id)
    assert (
        "Test User" not in row.signature_ciphertext
        and '"signature"' not in row.signature_ciphertext
    )
    url = f"{w['url']}/consents/{consent_id}/signature"
    evidence = checked(client.get(url, headers=w["headers"]))
    assert evidence["signer_name"] == "Test User" and len(evidence["signature"][0]) == 3
    assert evidence["quote_id"] == quote["id"]
    assert (
        client.get(
            url, headers=auth_headers(user_factory("other@example.com"))
        ).status_code
        == 404
    )
    different = {
        "quote_id": quote["id"],
        "text_version": row.text_version,
        "accepted": True,
        "signer_name": "Different Person",
        "signature": evidence["signature"],
    }
    assert (
        client.post(
            w["url"] + "/consents", headers=w["headers"], json=different
        ).status_code
        == 409
    )
    checked(
        client.post(
            w["url"] + "/submit",
            headers={**w["headers"], "Idempotency-Key": "signed-order-submit"},
            json=payload,
        )
    )
    staff = checked(
        client.get(w["staff_url"] + "/signed-consent", headers=w["staff_headers"])
    )
    assert staff["signer_name"] == "Test User"
    order = checked(client.get(w["url"], headers=w["headers"]))
    assert order["payment_status"] == "not_started"
    assert "signature" not in json.dumps(order)


@pytest.mark.parametrize(
    "changes",
    [
        {"signer_name": ""},
        {"signature": []},
        {"signature": [[{"x": 0.1, "y": 0.1}] * 3]},
        {"signature": [[{"x": 2, "y": 0}] * 3]},
        {"signature": '<svg onload="bad" />'},
    ],
)
def test_invalid_drawn_consent_rejected(client, workflow, changes):
    w = workflow
    quote = checked(
        client.post(
            w["url"] + "/quotes",
            headers=w["headers"],
            json={"expected_version": w["order"]["version"]},
        ),
        201,
    )
    from app.services.orders import CONSENT_VERSION

    payload = {
        "quote_id": quote["id"],
        "text_version": CONSENT_VERSION,
        "accepted": True,
        "signer_name": "Test User",
        "signature": [
            [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.6}, {"x": 0.9, "y": 0.1}]
        ],
        **changes,
    }
    assert (
        client.post(
            w["url"] + "/consents", headers=w["headers"], json=payload
        ).status_code
        == 422
    )
