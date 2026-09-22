from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.orders import now


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("order_id", "idempotency_key", name="uq_payment_order_key"),
        UniqueConstraint(
            "provider", "provider_reference", name="uq_payment_provider_reference"
        ),
        UniqueConstraint(
            "provider", "transaction_reference", name="uq_payment_transaction_reference"
        ),
        CheckConstraint("amount_minor > 0", name="ck_payment_positive_amount"),
        CheckConstraint(
            "refunded_minor >= 0 AND refunded_minor <= amount_minor",
            name="ck_payment_refund_total",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    active_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(10))
    account_fingerprint: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    status: Mapped[str] = mapped_column(String(30), default="initiating")
    provider_reference: Mapped[str | None] = mapped_column(String(150))
    transaction_reference: Mapped[str | None] = mapped_column(String(150))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(12))
    request_data: Mapped[dict] = mapped_column(JSON)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payment_attempts.id"), unique=True
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="initiating")
    provider_reference: Mapped[str | None] = mapped_column(String(150))
    correlation_id: Mapped[str | None] = mapped_column(String(150))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payment_attempts.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaymentWebhook(Base):
    __tablename__ = "payment_webhooks"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_payment_webhook_event"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    event_key: Mapped[str] = mapped_column(String(150))
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payment_attempts.id"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentLedger(Base):
    __tablename__ = "payment_ledger"
    __table_args__ = (
        UniqueConstraint("payment_id", "entry_key", name="uq_payment_ledger_entry"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payment_attempts.id"), index=True
    )
    entry_key: Mapped[str] = mapped_column(String(100))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
