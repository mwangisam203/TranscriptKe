from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.orders import now


class IssuedDocument(Base):
    __tablename__ = "issued_documents"
    __table_args__ = (
        CheckConstraint("mode IN ('demo','live')", name="ck_document_mode"),
        CheckConstraint("size BETWEEN 1 AND 2097152", name="ck_document_size"),
        CheckConstraint(
            "active_item_id IS NULL OR active_item_id = item_id",
            name="ck_document_active_item",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"))
    active_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_items.id"), unique=True
    )
    mode: Mapped[str] = mapped_column(String(10))
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    issued_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)


class DocumentDelivery(Base):
    __tablename__ = "document_deliveries"
    __table_args__ = (
        CheckConstraint("download_count >= 0", name="ck_delivery_download_count"),
        CheckConstraint(
            "notification_attempts >= 0", name="ck_delivery_notification_attempts"
        ),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("issued_documents.id"), unique=True
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_attempts: Mapped[int] = mapped_column(Integer, default=0)
    notification_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    code_hash: Mapped[str | None] = mapped_column(String(64))
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    last_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
