import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.access import ActionToken
from app.services.mail import Mailer


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(
    db: Session, mailer: Mailer, *, purpose: str, email: str, minutes: int, **fields
) -> ActionToken:
    raw = secrets.token_urlsafe(32)
    record = ActionToken(
        token_hash=token_digest(raw),
        purpose=purpose,
        email=email,
        expires_at=utcnow() + timedelta(minutes=minutes),
        **fields,
    )
    db.add(record)
    db.flush()
    mailer.send_token(email, purpose, raw, minutes)
    return record


def find_token(db: Session, raw: str, purpose: str) -> ActionToken:
    record = db.scalar(
        select(ActionToken).where(
            ActionToken.token_hash == token_digest(raw),
            ActionToken.purpose == purpose,
            ActionToken.consumed_at.is_(None),
            ActionToken.expires_at > utcnow(),
        )
    )
    if record is None:
        raise HTTPException(400, "Invalid or expired code")
    return record


def consume_token(db: Session, record: ActionToken) -> None:
    # Conditional write protects against simultaneous redemption in separate workers.
    result = db.execute(
        update(ActionToken)
        .where(
            ActionToken.id == record.id,
            ActionToken.consumed_at.is_(None),
            ActionToken.expires_at > utcnow(),
        )
        .values(consumed_at=utcnow())
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise HTTPException(400, "Invalid or expired code")


def invalidate_user_tokens(db: Session, user_id: int, purpose: str) -> None:
    db.execute(
        update(ActionToken)
        .where(
            ActionToken.user_id == user_id,
            ActionToken.purpose == purpose,
            ActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=utcnow())
    )
