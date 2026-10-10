"""Transactional outbox: a confirmed charge has one immutable receipt."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.orders import now


class PaymentReceiptEmail(Base):
    __tablename__ = "payment_receipt_emails"
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payment_attempts.id"), primary_key=True
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(255))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
