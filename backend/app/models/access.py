from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MembershipRole(StrEnum):
    STAFF = "staff"
    MANAGER = "manager"


class InstitutionMembership(Base):
    __tablename__ = "institution_memberships"
    __table_args__ = (
        UniqueConstraint(
            "institution_id", "user_id", name="uq_membership_institution_user"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(
            MembershipRole,
            native_enum=False,
            create_constraint=True,
            name="membership_role",
            values_callable=lambda roles: [role.value for role in roles],
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ActionToken(Base):
    """Only the digest is stored. Raw tokens are delivered to the intended mailbox."""

    __tablename__ = "action_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(30), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id"))
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    membership_role: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)


class AccessEvent(Base):
    __tablename__ = "access_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(50))
    subject_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
