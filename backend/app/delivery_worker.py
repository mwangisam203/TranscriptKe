"""Send pending recipient notifications; SMTP acceptance is not a delivery receipt."""

import argparse
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.db.session import SessionLocal
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.services.issuance import aware, locked_delivery, notify_delivery
from app.services.mail import Mailer
from app.services.orders import utcnow


def run(limit=100):
    cutoff = utcnow() - timedelta(minutes=5)
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(DocumentDelivery.id)
                .join(IssuedDocument)
                .where(
                    DocumentDelivery.notified_at.is_(None),
                    DocumentDelivery.expires_at > utcnow(),
                    IssuedDocument.revoked_at.is_(None),
                    or_(
                        DocumentDelivery.notification_attempted_at.is_(None),
                        DocumentDelivery.notification_attempted_at < cutoff,
                    ),
                )
                .order_by(
                    DocumentDelivery.notification_attempted_at.asc().nullsfirst(),
                    DocumentDelivery.id,
                )
                .limit(limit)
            )
        )
    sent = failed = 0
    for identifier in ids:
        with SessionLocal() as db:
            try:
                order, doc, delivery = locked_delivery(db, identifier)
                if delivery.notified_at or (
                    delivery.notification_attempted_at
                    and aware(delivery.notification_attempted_at) >= cutoff
                ):
                    continue
                notify_delivery(db, order, doc, delivery, Mailer())
                sent += 1
            except HTTPException:
                db.rollback()
                failed += 1
    return {"sent": sent, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=100, choices=range(1, 1001), metavar="1..1000"
    )
    result = run(parser.parse_args().limit)
    print(result)
    raise SystemExit(1 if result["failed"] else 0)
