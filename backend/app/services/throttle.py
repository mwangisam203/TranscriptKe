import hashlib
import time

from fastapi import HTTPException, Request
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.access import AuthRateLimit

WINDOW_SECONDS = 15 * 60


def enforce_rate_limit(
    db: Session,
    request: Request,
    action: str,
    email: str | None = None,
    account_limit: int = 10,
    ip_limit: int = 60,
) -> None:
    """Shared database counters, not process-local limits. Call before mutations."""
    window = int(time.time()) // WINDOW_SECONDS
    scopes = [
        (
            f"{action}:ip:{request.client.host if request.client else 'unknown'}",
            ip_limit,
        )
    ]
    if email:
        scopes.append((f"{action}:email:{email}", account_limit))
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    blocked = False
    for scope, limit in scopes:
        statement = (
            insert(AuthRateLimit)
            .values(
                key=hashlib.sha256(scope.encode()).hexdigest(),
                window=window,
                attempts=1,
            )
            .on_conflict_do_update(
                index_elements=[AuthRateLimit.key],
                set_={
                    "window": window,
                    "attempts": case(
                        (AuthRateLimit.window == window, AuthRateLimit.attempts + 1),
                        else_=1,
                    ),
                },
            )
            .returning(AuthRateLimit.attempts)
        )
        blocked |= db.scalar(statement) > limit
    db.execute(delete(AuthRateLimit).where(AuthRateLimit.window < window - 1))
    db.commit()
    if blocked:
        retry = WINDOW_SECONDS - int(time.time()) % WINDOW_SECONDS
        raise HTTPException(
            429,
            "Too many attempts; please try again later",
            headers={"Retry-After": str(retry)},
        )
