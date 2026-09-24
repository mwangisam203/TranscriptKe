from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.orders import now


class OperationsCase(Base):
    __tablename__ = "operations_cases"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    note: Mapped[str] = mapped_column(Text)
    follow_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkerRun(Base):
    __tablename__ = "worker_runs"
    __table_args__ = (
        CheckConstraint("worker IN ('payments','deliveries')", name="ck_worker_name"),
        CheckConstraint(
            "status IN ('running','succeeded','failed')", name="ck_worker_status"
        ),
        CheckConstraint("processed >= 0 AND failed >= 0", name="ck_worker_counts"),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    worker: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
