"""Retry committed receipt emails independently of payment settlement."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.db.session import SessionLocal
from app.models.receipts import PaymentReceiptEmail
from app.services.mail import Mailer
from app.services.orders import utcnow
from app.services.receipts import render_pdf
from app.services.worker_runs import tracked_run


def run(limit=25):
    with tracked_run(SessionLocal, "receipts") as counts:
        return _run(limit, counts)


def _run(limit, counts):
    sent = failed = 0
    cutoff = utcnow() - timedelta(minutes=1)
    with SessionLocal() as db:
        # SMTP has a ten-second timeout. Bound each batch below the task time limit.
        ids = list(
            db.scalars(
                select(PaymentReceiptEmail.payment_id)
                .where(
                    PaymentReceiptEmail.sent_at.is_(None),
                    or_(
                        PaymentReceiptEmail.last_attempt_at.is_(None),
                        PaymentReceiptEmail.last_attempt_at < cutoff,
                    ),
                )
                .order_by(PaymentReceiptEmail.created_at)
                .limit(min(limit, 15))
            )
        )
    for identifier in ids:
        with SessionLocal() as db:
            row = db.scalar(
                select(PaymentReceiptEmail)
                .where(
                    PaymentReceiptEmail.payment_id == identifier,
                    PaymentReceiptEmail.sent_at.is_(None),
                    or_(
                        PaymentReceiptEmail.last_attempt_at.is_(None),
                        PaymentReceiptEmail.last_attempt_at < cutoff,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                continue
            row.last_attempt_at = utcnow()
            row.attempt_count += 1
            counts["processed"] += 1
            try:
                Mailer().send_payment_receipt(
                    row.recipient_email, row.snapshot, render_pdf(row.snapshot)
                )
            except HTTPException:
                failed += 1
                counts["failed"] += 1
            else:
                row.sent_at = utcnow()
                sent += 1
            db.commit()
    return {"sent": sent, "failed": failed}
