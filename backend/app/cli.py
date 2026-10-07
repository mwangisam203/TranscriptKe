"""Explicit local administration; never invoked automatically on application startup."""

import argparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.academic import InstitutionService, OrderingPolicy
from app.models.access import AccessEvent
from app.models.billing import InstitutionBilling
from app.models.institution import Institution
from app.models.user import User, UserRole
from app.schemas.auth import EmailRequest
from app.services.mail import Mailer

DEVELOPMENT_INSTITUTIONS = (
    ("DEMO-UNI", "Demo Kenya University (development only)"),
    ("DEMO-TVET", "Demo Kenya Technical College (development only)"),
    ("DEMO-LAKEVIEW", "Demo Lakeview University (development only)"),
    ("DEMO-HIGHLAND", "Demo Highland University (development only)"),
    ("DEMO-COAST", "Demo Coast Technical College (development only)"),
    ("DEMO-RIVER", "Demo Riverbend College (development only)"),
    ("DEMO-SAVANNAH", "Demo Savannah University (development only)"),
    ("DEMO-GREENFIELD", "Demo Greenfield Polytechnic (development only)"),
    ("DEMO-SUNRISE", "Demo Sunrise College (development only)"),
    ("DEMO-VALLEY", "Demo Valley Institute of Technology (development only)"),
)


def seed_institutions(db: Session) -> int:
    if settings.APP_ENV != "development":
        raise ValueError(
            "Development seed data is only allowed with APP_ENV=development"
        )
    created = 0
    for code, name in DEVELOPMENT_INSTITUTIONS:
        if db.scalar(select(Institution.id).where(Institution.code == code)) is None:
            db.add(
                Institution(
                    code=code,
                    name=name,
                    country="Kenya",
                    is_active=True,
                    is_approved=True,
                )
            )
            created += 1
    db.commit()
    return created


def seed_academic_demo(db: Session) -> None:
    """Explicit fictional catalog; never overwrites a manager's configuration."""
    seed_institutions(db)
    for code, _ in DEVELOPMENT_INSTITUTIONS:
        institution = db.scalar(select(Institution).where(Institution.code == code))
        if db.get(InstitutionBilling, institution.id) is None:
            db.add(InstitutionBilling(institution_id=institution.id, enabled=True))
        if db.get(OrderingPolicy, institution.id) is None:
            db.add(
                OrderingPolicy(
                    institution_id=institution.id,
                    accepting_requests=True,
                    required_fields=["program", "attendance_start_year"],
                    student_instructions="Demo only: provide your admission number or ID/passport, name on record, program and attendance start month and year. No real academic records are connected.",
                )
            )
        if (
            db.scalar(
                select(InstitutionService.id).where(
                    InstitutionService.institution_id == institution.id,
                    InstitutionService.code == "DEMO-TRANSCRIPT",
                )
            )
            is None
        ):
            db.add(
                InstitutionService(
                    institution_id=institution.id,
                    code="DEMO-TRANSCRIPT",
                    name="Official transcript (demo)",
                    document_type="official_transcript",
                    description="Fictional service and sample fee for development. Use test checkout only.",
                    fee_minor=500,
                    currency="KES",
                    processing_days_min=3,
                    processing_days_max=7,
                    delivery_methods=["secure_electronic"],
                    required_fields=[],
                    is_active=True,
                )
            )
    db.commit()


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
    commands.add_parser(
        "seed-academic-demo",
        help="Insert fictional institutions, catalog and matching policies",
    )
    admin = commands.add_parser(
        "bootstrap-admin", help="Grant a verified account platform-admin access"
    )
    admin.add_argument("--email", required=True)
    test_mail = commands.add_parser(
        "test-email",
        help="Submit a test email using configured SMTP (no database writes)",
    )
    test_mail.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "test-email":
        if settings.MAIL_BACKEND != "smtp":
            parser.error(
                "Set MAIL_BACKEND=smtp and configure your provider in backend/.env first. File mode does not send to inboxes."
            )
        try:
            email = EmailRequest(email=args.email).email
            Mailer().send_test(email)
        except (ValueError, HTTPException) as exc:
            parser.error(
                exc.detail
                if isinstance(exc, HTTPException)
                else "Enter a valid recipient email address"
            )
        print(
            "Test email accepted by SMTP. Check your inbox and spam folder; inbox delivery is not guaranteed by SMTP acceptance."
        )
        return
    with SessionLocal() as db:
        try:
            if args.command == "seed-institutions":
                print(f"Created {seed_institutions(db)} development institutions.")
            elif args.command == "seed-academic-demo":
                seed_academic_demo(db)
                print("Demo institutions, services and matching policies are ready.")
            else:
                bootstrap_admin(db, args.email)
                print("Administrator access granted. Sign in again.")
        except ValueError as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
