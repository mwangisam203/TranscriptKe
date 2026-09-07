import stat
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select, text

from alembic import command
from app.cli import bootstrap_admin, seed_institutions
from app.core.config import Settings, settings
from app.models.access import AccessEvent
from app.models.institution import Institution
from app.models.user import UserRole
from app.services.mail import Mailer
from tests.conftest import migration_config


def test_fresh_database_matches_models_and_can_roundtrip(engine):
    with engine.begin() as connection:
        config = migration_config(connection)
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        )
        command.check(config)
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        command.check(config)


def test_upgrade_preserves_legacy_accounts_but_requires_real_verification(engine):
    with engine.begin() as connection:
        config = migration_config(connection)
        command.downgrade(config, "0001")
        connection.execute(
            text(
                "INSERT INTO users (email, is_email_verified, password_hash, full_name, role, created_at) "
                "VALUES (:email, :verified, :hash, :name, :role, :created)"
            ),
            {
                "email": "legacy@example.com",
                "verified": True,
                "hash": "existing-hash",
                "name": "Existing User",
                "role": "institution_staff",
                "created": datetime.now(timezone.utc),
            },
        )
        command.upgrade(config, "head")
        user = connection.execute(text("SELECT * FROM users")).mappings().one()
        assert (
            user["email"] == "legacy@example.com"
            and user["password_hash"] == "existing-hash"
        )
        assert not user["is_email_verified"] and user["token_version"] == 0
        assert (
            connection.scalar(text("SELECT count(*) FROM institution_memberships")) == 0
        )


def test_seed_is_repeatable_and_does_not_overwrite_existing_data(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert seed_institutions(db) == 2
    institution = db.scalar(select(Institution).where(Institution.code == "DEMO-UNI"))
    institution.is_active = False
    db.commit()
    assert seed_institutions(db) == 0
    db.refresh(institution)
    assert institution.is_active is False


def test_seed_rejected_outside_development(db):
    with pytest.raises(ValueError, match="only allowed"):
        seed_institutions(db)


def test_bootstrap_requires_verified_account_and_revokes_old_tokens(
    db, user_factory, auth_headers, client
):
    user = user_factory(verified=False)
    with pytest.raises(ValueError, match="Register and verify"):
        bootstrap_admin(db, user.email)
    user.is_email_verified = True
    db.commit()
    old_headers = auth_headers(user)
    bootstrap_admin(db, " STUDENT@EXAMPLE.COM ")
    db.refresh(user)
    assert user.role == UserRole.ADMIN and user.token_version == 1
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 401
    bootstrap_admin(db, user.email)
    assert len(db.scalars(select(AccessEvent)).all()) == 1


def test_inactive_institutions_are_not_public(client, db, institutions):
    institutions[1].is_active = False
    db.commit()
    response = client.get("/api/v1/institutions")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [institutions[0].id]
    assert client.get(f"/api/v1/institutions/{institutions[1].id}").status_code == 404


def test_local_mail_is_private_and_contains_code(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_DIRECTORY", tmp_path / "mailbox")
    Mailer().send_token(
        "student@example.com", "email_verification", "one-time-test-code", 60
    )
    files = list(settings.MAIL_DIRECTORY.glob("*.eml"))
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert "one-time-test-code" in files[0].read_text()
    assert "student@example.com" in files[0].read_text()


@pytest.mark.parametrize(
    "overrides",
    [
        {"SECRET_KEY": "short"},
        {"MAIL_BACKEND": "file"},
        {"SMTP_STARTTLS": False},
        {"SMTP_HOST": None},
    ],
)
def test_unsafe_production_settings_rejected(overrides):
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql://localhost/test",
        "SECRET_KEY": "a" * 48,
        "MAIL_BACKEND": "smtp",
        "SMTP_HOST": "smtp.example.com",
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, **overrides})
