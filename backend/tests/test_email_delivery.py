"""Exercise the real mailer without contacting a provider or reading credentials."""

import smtplib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app import cli
from app.core.config import settings
from app.main import app
from app.models.user import User
from app.services.mail import Mailer, get_mailer
from tests.conftest import PASSWORD


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_STARTTLS", True)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "test-sender")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "private-test-password")
    monkeypatch.setattr(settings, "MAIL_FROM", "sender@example.com")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr("app.services.mail.smtplib.SMTP", factory)
    return factory, connection


def test_registration_submits_verification_to_smtp(client, smtp):
    factory, connection = smtp
    app.dependency_overrides[get_mailer] = Mailer
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "student@example.com",
            "full_name": "Student",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201
    factory.assert_called_once_with("smtp.example.com", 587, timeout=10)
    connection.starttls.assert_called_once()
    connection.login.assert_called_once_with("test-sender", "private-test-password")
    message = connection.send_message.call_args.args[0]
    assert message["To"] == "student@example.com"
    assert message["From"] == "sender@example.com"
    code = message.get_content().split("\n\n")[1].strip()
    assert code not in response.text
    assert (
        client.post(
            "/api/v1/auth/email-verifications/confirm", json={"token": code}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/email-verifications/confirm", json={"token": code}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "student@example.com", "password": PASSWORD},
        ).status_code
        == 200
    )


def test_failed_smtp_submission_rolls_back_registration(client, db, smtp):
    _, connection = smtp
    app.dependency_overrides[get_mailer] = Mailer
    connection.send_message.side_effect = smtplib.SMTPException(
        "private provider error"
    )
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "student@example.com",
            "full_name": "Student",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 503
    assert "private provider error" not in response.text
    assert db.scalar(select(User).where(User.email == "student@example.com")) is None


def test_test_email_command_requires_smtp(monkeypatch, capsys):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "file")
    monkeypatch.setattr(
        "sys.argv", ["cli", "test-email", "--email", "student@example.com"]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "File mode does not send to inboxes" in capsys.readouterr().err


def test_test_email_command_submits_no_verification_token(monkeypatch, smtp, capsys):
    _, connection = smtp
    monkeypatch.setattr(
        "sys.argv", ["cli", "test-email", "--email", "student@example.com"]
    )
    monkeypatch.setattr(
        cli,
        "SessionLocal",
        MagicMock(side_effect=AssertionError("No DB session expected")),
    )
    cli.main()
    message = connection.send_message.call_args.args[0]
    assert message["Subject"] == "TranscriptsKE: email delivery test"
    assert "This is not a verification code" in message.get_content()
    assert "accepted by SMTP" in capsys.readouterr().out
