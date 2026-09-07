import os
from pathlib import Path
from uuid import uuid4

# Never connect tests to the developer's configured application database or mail provider.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "isolated-test-secret-key-at-least-32-characters"
os.environ["MAIL_BACKEND"] = "file"

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db
from app.main import app
from app.models.institution import Institution
from app.models.user import User, UserRole
from app.services.mail import get_mailer

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse 123"


def migration_config(connection):
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    admin_engine = None
    schema = None
    if url:
        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql" or not (
            parsed.database or ""
        ).startswith("transcriptske_test"):
            raise ValueError(
                "TEST_DATABASE_URL must be PostgreSQL with a database named transcriptske_test*"
            )
        schema = "test_" + uuid4().hex
        admin_engine = create_engine(url)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        result = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        result = create_engine(
            f"sqlite:///{tmp_path / 'test.sqlite3'}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(result, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    with result.begin() as connection:
        command.upgrade(migration_config(connection), "head")
    yield result
    result.dispose()
    if admin_engine:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


class FakeMailer:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send_token(self, email, purpose, token, minutes):
        if self.fail:
            from fastapi import HTTPException

            raise HTTPException(503, "Email delivery is unavailable; please try again")
        self.messages.append({"email": email, "purpose": purpose, "token": token})

    def token(self, purpose, email=None):
        return next(
            message["token"]
            for message in reversed(self.messages)
            if message["purpose"] == purpose
            and (email is None or message["email"] == email)
        )


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def client(engine, mailer):
    def test_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_mailer] = lambda: mailer
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user_factory(db):
    # One hash per test fixture keeps authorization tests focused and reasonably fast.
    password_hash = get_password_hash(PASSWORD)

    def create(email="student@example.com", role=UserRole.STUDENT, verified=True):
        user = User(
            email=email,
            password_hash=password_hash,
            full_name="Test User",
            is_email_verified=verified,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return create


@pytest.fixture
def auth_headers():
    def headers(user):
        return {
            "Authorization": f"Bearer {create_access_token(str(user.id), user.token_version)}"
        }

    return headers


@pytest.fixture
def institutions(db):
    items = [
        Institution(name="Test University A", code="TEST-A"),
        Institution(name="Test College B", code="TEST-B"),
    ]
    db.add_all(items)
    db.commit()
    for item in items:
        db.refresh(item)
    return items
