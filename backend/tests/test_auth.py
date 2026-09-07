from datetime import timedelta

import pytest
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.models.access import ActionToken
from app.models.user import User
from app.services.tokens import token_digest, utcnow
from tests.conftest import PASSWORD

AUTH = "/api/v1/auth"
REGISTER = {
    "email": "student@example.com",
    "password": PASSWORD,
    "full_name": "  Jane   Doe  ",
}


def test_registration_verification_and_login(client, mailer, db):
    response = client.post(
        f"{AUTH}/register", json={**REGISTER, "email": " STUDENT@EXAMPLE.COM "}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "student"
    assert data["full_name"] == "Jane Doe"
    assert data["email"] == REGISTER["email"]
    assert data["is_email_verified"] is False
    assert "password_hash" not in data and "token" not in data
    login = {"email": REGISTER["email"], "password": PASSWORD}
    assert client.post(f"{AUTH}/login", json=login).status_code == 403
    raw = mailer.token("email_verification")
    stored = db.scalar(select(ActionToken))
    assert stored.token_hash == token_digest(raw) and stored.token_hash != raw
    assert (
        client.post(
            f"{AUTH}/email-verifications/confirm", json={"token": raw}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{AUTH}/email-verifications/confirm", json={"token": raw}
        ).status_code
        == 400
    )
    response = client.post(f"{AUTH}/login", json=login)
    assert response.status_code == 200
    me = client.get(
        f"{AUTH}/me",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    )
    assert me.status_code == 200 and me.json()["is_email_verified"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "invalid"},
        {"email": ""},
        {"full_name": " \t "},
        {"password": "short"},
        {"password": "é" * 37},
        {"role": "admin"},
        {"is_email_verified": True},
        {"institution_id": 1},
    ],
)
def test_registration_rejects_invalid_or_privileged_fields(client, changes):
    assert (
        client.post(f"{AUTH}/register", json={**REGISTER, **changes}).status_code == 422
    )


def test_duplicate_registration_normalizes_email(client, user_factory):
    user_factory()
    response = client.post(
        f"{AUTH}/register", json={**REGISTER, "email": "STUDENT@EXAMPLE.COM"}
    )
    assert response.status_code == 409


def test_mail_failure_rolls_back_registration(client, mailer, db):
    mailer.fail = True
    assert client.post(f"{AUTH}/register", json=REGISTER).status_code == 503
    assert db.scalar(select(User)) is None
    assert db.scalar(select(ActionToken)) is None


@pytest.mark.parametrize(
    "email,password",
    [
        ("unknown@example.com", PASSWORD),
        ("student@example.com", "wrong"),
        ("student@example.com", "x" * 73),
        ("student@example.com", "é" * 60),
    ],
)
def test_invalid_login_is_consistent(client, user_factory, email, password):
    user_factory()
    response = client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_verification_expiry_and_resend(client, mailer, user_factory, db):
    user_factory(verified=False)
    payload = {"email": "student@example.com"}
    assert client.post(f"{AUTH}/email-verifications", json=payload).status_code == 202
    raw = mailer.token("email_verification")
    record = db.scalar(select(ActionToken))
    record.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert (
        client.post(
            f"{AUTH}/email-verifications/confirm", json={"token": raw}
        ).status_code
        == 400
    )
    client.post(f"{AUTH}/email-verifications", json=payload)
    fresh = mailer.token("email_verification")
    assert fresh != raw
    assert (
        client.post(
            f"{AUTH}/email-verifications/confirm", json={"token": fresh}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("endpoint", ["email-verifications", "password-reset-requests"])
def test_email_requests_do_not_disclose_account_existence(
    client, user_factory, endpoint
):
    user_factory(verified=False)
    known = client.post(f"{AUTH}/{endpoint}", json={"email": "student@example.com"})
    unknown = client.post(f"{AUTH}/{endpoint}", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_reset_invalidates_sessions_and_all_reset_codes(
    client, user_factory, auth_headers, mailer
):
    user = user_factory()
    headers = auth_headers(user)
    for _ in range(2):
        client.post(f"{AUTH}/password-reset-requests", json={"email": user.email})
    first, second = [message["token"] for message in mailer.messages]
    payload = {"token": second, "password": "new password 123"}
    assert client.post(f"{AUTH}/password-resets", json=payload).status_code == 200
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 401
    for token in [first, second]:
        assert (
            client.post(
                f"{AUTH}/password-resets", json={**payload, "token": token}
            ).status_code
            == 400
        )
    assert (
        client.post(
            f"{AUTH}/login", json={"email": user.email, "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"{AUTH}/login", json={"email": user.email, "password": payload["password"]}
        ).status_code
        == 200
    )


def test_reset_does_not_bypass_verification_and_tokens_are_purpose_bound(
    client, user_factory, mailer, db
):
    user = user_factory(verified=False)
    client.post(f"{AUTH}/password-reset-requests", json={"email": user.email})
    raw = mailer.token("password_reset")
    assert (
        client.post(
            f"{AUTH}/email-verifications/confirm", json={"token": raw}
        ).status_code
        == 400
    )
    payload = {"token": raw, "password": "new password 123"}
    assert client.post(f"{AUTH}/password-resets", json=payload).status_code == 200
    assert (
        client.post(
            f"{AUTH}/login", json={"email": user.email, "password": payload["password"]}
        ).status_code
        == 403
    )
    db.refresh(user)
    assert not user.is_email_verified


def test_expired_reset_is_rejected(client, user_factory, mailer, db):
    user = user_factory()
    client.post(f"{AUTH}/password-reset-requests", json={"email": user.email})
    record = db.scalar(select(ActionToken))
    record.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert (
        client.post(
            f"{AUTH}/password-resets",
            json={
                "token": mailer.token("password_reset"),
                "password": "new password 123",
            },
        ).status_code
        == 400
    )


def test_password_change_requires_current_password_and_revokes_access(
    client, user_factory, auth_headers
):
    user = user_factory()
    headers = auth_headers(user)
    payload = {"current_password": "wrong", "password": "replacement password"}
    assert (
        client.put(f"{AUTH}/password", headers=headers, json=payload).status_code == 400
    )
    payload["current_password"] = PASSWORD
    assert (
        client.put(f"{AUTH}/password", headers=headers, json=payload).status_code == 200
    )
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 401


def test_logout_revokes_access(client, user_factory, auth_headers):
    headers = auth_headers(user_factory())
    assert client.post(f"{AUTH}/logout", headers=headers).status_code == 200
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "nonsense"},
        {"sub": []},
        {"type": "password_reset"},
        {"ver": "0"},
        {"exp": 1},
        {"sub": "99999999999999999999999999999999999999"},
    ],
)
def test_malformed_or_expired_jwt_returns_401(client, user_factory, claims):
    user = user_factory()
    payload = {
        "sub": str(user.id),
        "type": "access",
        "ver": 0,
        "iat": utcnow(),
        "exp": utcnow() + timedelta(minutes=5),
        **claims,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    assert (
        client.get(
            f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )


def test_missing_or_incomplete_token_returns_401(client):
    assert client.get(f"{AUTH}/me").status_code == 401
    for raw in [
        "garbage",
        jwt.encode({"sub": "1"}, settings.SECRET_KEY, algorithm="HS256"),
    ]:
        assert (
            client.get(
                f"{AUTH}/me", headers={"Authorization": f"Bearer {raw}"}
            ).status_code
            == 401
        )


def test_login_rate_limit(client, user_factory):
    user_factory()
    for _ in range(10):
        response = client.post(
            f"{AUTH}/login", json={"email": "student@example.com", "password": "wrong"}
        )
        assert response.status_code == 401
    response = client.post(
        f"{AUTH}/login", json={"email": "student@example.com", "password": "wrong"}
    )
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
