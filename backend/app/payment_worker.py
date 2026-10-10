"""Run with python -m app.payment_worker. Reconciles existing requests; never creates charges."""

import argparse
import time
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.v1.payments import locked_system_payment
from app.db.session import engine
from app.models.payments import PaymentAttempt
from app.services.orders import utcnow
from app.services.payment_gateways import get_gateways
from app.services.payments import MPESA_CONFIRMATION_SECONDS, OPEN, reconcile
from app.services.worker_runs import tracked_run


def run(limit=100):
    with tracked_run(lambda: Session(engine), "payments") as counts:
        return _run(limit, counts)


def _run(limit, counts):
    gateway = get_gateways()
    with Session(engine) as db:
        identifiers = db.scalars(
            select(PaymentAttempt.id)
            .where(
                PaymentAttempt.status.notin_(["failed", "expired", "refunded"]),
                or_(
                    and_(
                        PaymentAttempt.provider_reference.is_not(None),
                        or_(
                            PaymentAttempt.checked_at.is_(None),
                            and_(
                                PaymentAttempt.status.in_(OPEN),
                                PaymentAttempt.checked_at
                                < utcnow() - timedelta(seconds=5),
                            ),
                            and_(
                                PaymentAttempt.status.notin_(OPEN),
                                PaymentAttempt.checked_at
                                < utcnow() - timedelta(minutes=5),
                            ),
                        ),
                    ),
                    and_(
                        PaymentAttempt.provider == "mpesa",
                        PaymentAttempt.provider_reference.is_(None),
                        PaymentAttempt.status.in_(OPEN),
                        PaymentAttempt.created_at
                        <= utcnow() - timedelta(seconds=MPESA_CONFIRMATION_SECONDS),
                    ),
                ),
            )
            .order_by(
                PaymentAttempt.checked_at.asc().nullsfirst(), PaymentAttempt.created_at
            )
            .limit(limit)
        ).all()
    success = failed = 0
    for identifier in identifiers:
        with Session(engine) as db:
            try:
                order, payment = locked_system_payment(db, identifier, require_provider=False)
                reconcile(db, order, payment, gateway)
                counts["processed"] += 1
                if payment.status in OPEN and not payment.provider_reference:
                    failed += 1
                    counts["failed"] += 1
                else:
                    success += 1
            except HTTPException:
                db.rollback()
                failed += 1
                counts["processed"] += 1
                counts["failed"] += 1
    print(
        f"Reconciled {success} payment(s); {failed} require another attempt or provider investigation."
    )
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--loop", action="store_true", help="Keep reconciling every 10 seconds"
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    if args.loop:
        try:
            while True:
                run(args.limit)
                time.sleep(10)
        except KeyboardInterrupt:
            pass
    else:
        raise SystemExit(1 if run(args.limit) else 0)
