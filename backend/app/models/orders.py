from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def now():
    return datetime.now(timezone.utc)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "creation_key", name="uq_order_user_creation_key"),
        CheckConstraint(
            "status IN ('draft','submitted','cancellation_requested','cancelled')",
            name="ck_order_status",
        ),
        CheckConstraint("version >= 1", name="ck_order_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(
        String(40), unique=True, default=lambda: "TRK-" + uuid4().hex[:16].upper()
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    academic_record_link_id: Mapped[int] = mapped_column(
        ForeignKey("academic_record_links.id")
    )
    creation_key: Mapped[str] = mapped_column(String(100))
    creation_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(1000), default="")
    release_when: Mapped[str] = mapped_column(String(30), default="now")
    release_instruction: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    payment_status: Mapped[str] = mapped_column(String(30), default="not_started")
    version: Mapped[int] = mapped_column(Integer, default=1)
    submitted_snapshot: Mapped[dict | None] = mapped_column(JSON)
    submission_key: Mapped[str | None] = mapped_column(String(100))
    submission_quote_id: Mapped[str | None] = mapped_column(String(36))
    submission_consent_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderRecipient(Base):
    __tablename__ = "order_recipients"
    __table_args__ = (
        UniqueConstraint("order_id", "key", name="uq_order_recipient_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    organization: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str | None] = mapped_column(String(255))
    delivery_method: Mapped[str] = mapped_column(String(30))
    postal_address: Mapped[dict | None] = mapped_column(JSON)
    application_reference: Mapped[str] = mapped_column(String(100), default="")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "key", name="uq_order_item_key"),
        UniqueConstraint(
            "order_id", "service_id", "recipient_key", name="uq_order_service_recipient"
        ),
        ForeignKeyConstraint(
            ["order_id", "recipient_key"],
            ["order_recipients.order_id", "order_recipients.key"],
            name="fk_item_order_recipient",
        ),
        CheckConstraint("quantity BETWEEN 1 AND 10", name="ck_order_item_quantity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    service_id: Mapped[int] = mapped_column(ForeignKey("institution_services.id"))
    recipient_key: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    fulfillment_status: Mapped[str] = mapped_column(String(30), default="draft")


class OrderAttachment(Base):
    __tablename__ = "order_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    scan_method: Mapped[str] = mapped_column(String(30))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderQuote(Base):
    __tablename__ = "order_quotes"
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    order_version: Mapped[int] = mapped_column(Integer)
    scope_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrderConsent(Base):
    __tablename__ = "order_consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("order_quotes.id"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text_version: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    scope_hash: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderEvent(Base):
    __tablename__ = "order_events"
    __table_args__ = (
        UniqueConstraint("order_id", "version", name="uq_order_event_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderMessage(Base):
    __tablename__ = "order_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_role: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    requires_response: Mapped[bool] = mapped_column(Boolean, default=False)
    in_reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("order_messages.id"))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderCancellation(Base):
    __tablename__ = "order_cancellations"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
