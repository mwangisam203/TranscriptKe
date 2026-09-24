"""Read-only configuration checks. No external provider requests or messages."""

import shutil
from datetime import timedelta, timezone
from email.utils import parseaddr
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.models.institution import Institution
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.operations import WorkerRun
from app.models.payments import PaymentAttempt
from app.services.orders import utcnow
from app.services.payment_gateways import configured, fingerprint


def expected_heads():
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    return set(ScriptDirectory.from_config(config).get_heads())


def database_ready(db):
    try:
        db.execute(text("SELECT 1"))
        return (
            set(db.scalars(text("SELECT version_num FROM alembic_version")))
            == expected_heads()
        )
    except SQLAlchemyError:
        db.rollback()
        return False


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def report(db, *, live=False):
    checks = []

    def add(name, passed, message):
        checks.append(
            {"name": name, "status": "ok" if passed else "blocked", "message": message}
        )

    healthy = database_ready(db)
    add(
        "database",
        healthy,
        "Database connection and migration head match."
        if healthy
        else "Database unavailable or migrations are not at head; run alembic upgrade head.",
    )
    add(
        "secret",
        len(settings.SECRET_KEY) >= 32
        and settings.SECRET_KEY != "change-this-secret-key",
        "Use a random application secret of at least 32 characters.",
    )
    if live:
        add(
            "environment",
            settings.APP_ENV == "production",
            "The live pilot requires APP_ENV=production.",
        )
        add(
            "live_features",
            settings.PAYMENTS_ENABLED
            and settings.ISSUANCE_ENABLED
            and settings.PAYMENT_MODE == "live"
            and settings.ISSUANCE_MODE == "live",
            "Enable live payment collection and live document issuance for the paid-delivery pilot.",
        )
    smtp = (
        settings.MAIL_BACKEND == "smtp"
        and bool(settings.SMTP_HOST)
        and settings.SMTP_STARTTLS
    )
    sender_address = parseaddr(settings.MAIL_FROM)[1].casefold()
    sender_domain = sender_address.rsplit("@", 1)[-1]
    sender = (
        "@" in sender_address
        and not sender_domain.endswith(".example")
        and sender_domain not in ("example.com", "example.org", "example.net")
    )
    add(
        "mail",
        bool(smtp and sender)
        if live or settings.ISSUANCE_MODE == "live"
        else settings.MAIL_BACKEND == "file" or bool(smtp and sender),
        "Mail transport configuration only; delivery and bounces must be tested separately.",
    )
    if settings.ISSUANCE_ENABLED or live:
        add(
            "scanner",
            settings.ATTACHMENT_SCANNER == "clamav"
            and bool(shutil.which(settings.CLAMAV_COMMAND)),
            "ClamAV must be configured and executable; this does not verify signature freshness or a real scan.",
        )
        if live:
            add(
                "public_origins",
                settings.ISSUANCE_PUBLIC_URL.startswith("https://")
                and settings.PAYMENT_PUBLIC_URL.startswith("https://"),
                "Live public origins must use HTTPS; external reachability and certificates require acceptance tests.",
            )
    if healthy:
        active_bindings = db.execute(
            select(
                PaymentAttempt.provider,
                PaymentAttempt.mode,
                PaymentAttempt.account_fingerprint,
            )
            .where(PaymentAttempt.status.notin_(["failed", "expired", "refunded"]))
            .distinct()
        ).all()
        active_providers = {row.provider for row in active_bindings}
        if settings.PAYMENTS_ENABLED or active_providers or live:
            methods = [
                p for p in ("stripe", "mpesa") if configured(p, collection=False)
            ]
            add(
                "payment_credentials",
                bool(methods) and active_providers.issubset(methods),
                "Configure a complete provider credential set for each provider with outstanding payments.",
            )
            add(
                "payment_binding",
                all(
                    row.mode == settings.PAYMENT_MODE
                    and row.account_fingerprint == fingerprint(row.provider)
                    for row in active_bindings
                ),
                "Outstanding payments must retain their original provider account and mode; investigate configuration changes before reconciliation.",
            )
            institution = (
                db.get(Institution, settings.PAYMENT_INSTITUTION_ID)
                if settings.PAYMENT_INSTITUTION_ID
                else None
            )
            add(
                "pilot_institution",
                bool(institution and institution.is_active and institution.is_approved),
                "The configured payment pilot institution must be active and approved.",
            )
        outstanding_deliveries = db.scalar(
            select(func.count(DocumentDelivery.id))
            .join(IssuedDocument)
            .where(
                DocumentDelivery.notified_at.is_(None),
                DocumentDelivery.expires_at > utcnow(),
                IssuedDocument.revoked_at.is_(None),
            )
        )
        workers = []
        if settings.PAYMENTS_ENABLED or active_providers:
            workers.append("payments")
        if settings.ISSUANCE_ENABLED or outstanding_deliveries:
            workers.append("deliveries")
        for worker in workers:
            latest = db.scalar(
                select(WorkerRun)
                .where(WorkerRun.worker == worker)
                .order_by(WorkerRun.started_at.desc(), WorkerRun.id.desc())
                .limit(1)
            )
            cutoff = utcnow() - timedelta(
                minutes=settings.OPERATIONS_WORKER_STALE_MINUTES
            )
            passed = bool(
                latest
                and latest.status == "succeeded"
                and latest.finished_at
                and aware(latest.finished_at) >= cutoff
            )
            if (
                latest
                and latest.status == "running"
                and aware(latest.started_at) >= cutoff
            ):
                previous = db.scalar(
                    select(WorkerRun)
                    .where(
                        WorkerRun.worker == worker, WorkerRun.finished_at.is_not(None)
                    )
                    .order_by(WorkerRun.finished_at.desc())
                    .limit(1)
                )
                passed = bool(
                    previous
                    and previous.status == "succeeded"
                    and aware(previous.finished_at) >= cutoff
                )
            add(
                f"worker_{worker}",
                passed,
                "Worker must complete successfully within the configured freshness window; missing, failed or stale runs require investigation.",
            )
            checks[-1].update(
                last_status=latest.status if latest else None,
                last_started_at=latest.started_at.isoformat() if latest else None,
                last_finished_at=latest.finished_at.isoformat()
                if latest and latest.finished_at
                else None,
            )
    return {
        "target": "live paid-delivery pilot" if live else "current configuration",
        "configuration_ready": all(c["status"] == "ok" for c in checks),
        "checks": checks,
        "manual_acceptance_required": [
            "Verify real provider checkout, callbacks, reconciliation and refunds in the appropriate provider environment.",
            "Verify a real malware scan, current signatures, SMTP receipt and recipient download/revocation.",
            "Verify HTTPS/proxy configuration and log redaction, backup restoration, access review and institution operating procedures.",
        ],
    }
