from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import reset_password
from app.models.access import ActionToken
from app.models.user import User
from app.schemas.auth import PasswordReset
from app.services.tokens import consume_token, find_token
from tests.conftest import PASSWORD


def test_code_can_only_be_consumed_once_across_database_connections(
    engine, client, user_factory, mailer
):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row concurrency is validated against PostgreSQL")
    user = user_factory()
    client.post("/api/v1/auth/password-reset-requests", json={"email": user.email})
    raw = mailer.token("password_reset")
    barrier = Barrier(2)

    def redeem():
        with Session(engine) as db:
            record = find_token(db, raw, "password_reset")
            barrier.wait(timeout=10)
            try:
                consume_token(db, record)
                db.commit()
                return 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: redeem(), range(2)))
    assert sorted(outcomes) == [200, 400]


def test_parallel_password_resets_cannot_both_succeed(
    engine, client, user_factory, mailer, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row concurrency is validated against PostgreSQL")
    user = user_factory()
    for _ in range(2):
        client.post("/api/v1/auth/password-reset-requests", json={"email": user.email})
    codes = [message["token"] for message in mailer.messages]
    barrier = Barrier(2)

    def synchronized_find(db, raw, purpose):
        record = find_token(db, raw, purpose)
        barrier.wait(timeout=10)
        return record

    monkeypatch.setattr("app.api.v1.auth.find_token", synchronized_find)
    # Counters are tested via HTTP separately; synchronise at the business transaction here.
    monkeypatch.setattr(
        "app.api.v1.auth.enforce_rate_limit", lambda *args, **kwargs: None
    )

    def reset(raw):
        with Session(engine) as db:
            try:
                reset_password(
                    PasswordReset(token=raw, password=PASSWORD + "!"), None, db
                )
                return 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(reset, codes))
    assert sorted(outcomes) == [200, 400]
    with Session(engine) as db:
        assert db.get(User, user.id).token_version == 1
        assert all(record.consumed_at for record in db.scalars(select(ActionToken)))
