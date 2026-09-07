"""Explicit local administration; never invoked automatically on application startup."""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.access import AccessEvent
from app.models.institution import Institution
from app.models.user import User, UserRole
from app.schemas.auth import EmailRequest

DEVELOPMENT_INSTITUTIONS = (
    ("DEMO-UNI", "Demo Kenya University (development only)"),
    ("DEMO-TVET", "Demo Kenya Technical College (development only)"),
)


def seed_institutions(db: Session) -> int:
    if settings.APP_ENV != "development":
        raise ValueError(
            "Development seed data is only allowed with APP_ENV=development"
        )
    created = 0
    for code, name in DEVELOPMENT_INSTITUTIONS:
        if db.scalar(select(Institution.id).where(Institution.code == code)) is None:
            db.add(Institution(code=code, name=name, country="Kenya", is_active=True))
            created += 1
    db.commit()
    return created


def bootstrap_admin(db: Session, email: str) -> None:
    normalized = EmailRequest(email=email).email
    user = db.scalar(select(User).where(User.email == normalized).with_for_update())
    if user is None or not user.is_email_verified:
        raise ValueError(
            "Register and verify this account before granting administrator access"
        )
    if user.role != UserRole.ADMIN:
        user.role = UserRole.ADMIN
        user.token_version += 1
        db.add(
            AccessEvent(
                actor_id=user.id, subject_id=user.id, action="admin_bootstrapped"
            )
        )
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "seed-institutions", help="Insert fictional development institutions"
    )
    admin = commands.add_parser(
        "bootstrap-admin", help="Grant a verified account platform-admin access"
    )
    admin.add_argument("--email", required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            if args.command == "seed-institutions":
                print(f"Created {seed_institutions(db)} development institutions.")
            else:
                bootstrap_admin(db, args.email)
                print("Administrator access granted. Sign in again.")
        except ValueError as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
