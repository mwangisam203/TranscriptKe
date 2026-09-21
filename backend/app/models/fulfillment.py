from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.orders import now


class RegistrarCase(Base):
    __tablename__ = "registrar_cases"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    release_confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    release_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    release_evidence: Mapped[str | None] = mapped_column(Text)


class OrderHold(Base):
    __tablename__ = "order_holds"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    category: Mapped[str] = mapped_column(String(30))
    student_message: Mapped[str] = mapped_column(Text)
    internal_note: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)


class RegistrarEvent(Base):
    __tablename__ = "registrar_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    order_version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    internal_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
