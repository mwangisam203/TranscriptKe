"""Private onboarding state and immutable pilot evidence with explicit review."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.orders import now


class InstitutionOnboarding(Base):
    __tablename__ = "institution_onboarding"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_onboarding_version"),
        CheckConstraint(
            "status IN ('draft','submitted','changes_requested','approved')",
            name="ck_onboarding_status",
        ),
    )
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PilotEvaluation(Base):
    __tablename__ = "pilot_evaluations"
    __table_args__ = (
        CheckConstraint("mode IN ('demo','live')", name="ck_pilot_mode"),
        CheckConstraint(
            "decision IN ('pending','continue_pilot','rework','expand')",
            name="ck_pilot_decision",
        ),
        CheckConstraint("window_end > window_start", name="ck_pilot_window"),
        CheckConstraint("version >= 1", name="ck_pilot_version"),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(10))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict] = mapped_column(JSON)
    findings: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decision: Mapped[str] = mapped_column(String(30), default="pending")
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_snapshot: Mapped[dict | None] = mapped_column(JSON)
