from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentType(StrEnum):
    OFFICIAL_TRANSCRIPT = "official_transcript"
    STUDENT_COPY = "student_copy"
    DEGREE_CERTIFICATE = "degree_certificate"
    ENROLLMENT_LETTER = "enrollment_letter"
    COMPLETION_LETTER = "completion_letter"


class DeliveryMethod(StrEnum):
    SECURE_ELECTRONIC = "secure_electronic"
    COLLECTION = "collection"
    POST = "post"


class MatchingField(StrEnum):
    PROGRAM = "program"
    ATTENDANCE_START_YEAR = "attendance_start_year"
    ATTENDANCE_END_YEAR = "attendance_end_year"
    PREVIOUS_NAMES = "previous_names"


class MatchStatus(StrEnum):
    PENDING = "pending"
    NEEDS_INFORMATION = "needs_information"
    MATCHED = "matched"
    REJECTED = "rejected"


class InstitutionService(Base):
    __tablename__ = "institution_services"
    __table_args__ = (
        UniqueConstraint("institution_id", "code", name="uq_service_institution_code"),
        CheckConstraint(
            "fee_minor >= 0 AND fee_minor <= 1000000000", name="ck_service_fee"
        ),
        CheckConstraint("currency = 'KES'", name="ck_service_currency"),
        CheckConstraint(
            "processing_days_min >= 0 AND processing_days_max >= processing_days_min AND processing_days_max <= 365",
            name="ck_service_processing_days",
        ),
        CheckConstraint("version >= 1", name="ck_service_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text, default="")
    fee_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    processing_days_min: Mapped[int] = mapped_column(Integer)
    processing_days_max: Mapped[int] = mapped_column(Integer)
    delivery_methods: Mapped[list[str]] = mapped_column(JSON)
    required_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class OrderingPolicy(Base):
    __tablename__ = "ordering_policies"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_policy_version"),)

    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), primary_key=True
    )
    accepting_requests: Mapped[bool] = mapped_column(Boolean, default=False)
    student_instructions: Mapped[str] = mapped_column(Text, default="")
    required_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class AcademicRecordLink(Base):
    __tablename__ = "academic_record_links"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "institution_id",
            "admission_number",
            name="uq_link_user_institution_admission",
        ),
        CheckConstraint(
            "status IN ('pending', 'needs_information', 'matched', 'rejected')",
            name="ck_link_status",
        ),
        CheckConstraint("version >= 1", name="ck_link_version"),
        CheckConstraint(
            "status != 'matched' OR record_reference IS NOT NULL",
            name="ck_matched_record_reference",
        ),
        Index(
            "uq_matched_institution_record",
            "institution_id",
            "record_reference",
            unique=True,
            postgresql_where=text("status = 'matched'"),
            sqlite_where=text("status = 'matched'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    service_id: Mapped[int] = mapped_column(ForeignKey("institution_services.id"))
    admission_number: Mapped[str] = mapped_column(String(100))
    name_on_record: Mapped[str] = mapped_column(String(255))
    program: Mapped[str | None] = mapped_column(String(255))
    attendance_start_year: Mapped[int | None] = mapped_column(Integer)
    attendance_end_year: Mapped[int | None] = mapped_column(Integer)
    previous_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    requirements_snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default=MatchStatus.PENDING.value)
    student_message: Mapped[str | None] = mapped_column(Text)
    record_reference: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RecordMatchEvent(Base):
    __tablename__ = "record_match_events"
    __table_args__ = (
        UniqueConstraint("link_id", "version", name="uq_match_event_link_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(
        ForeignKey("academic_record_links.id"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    student_message: Mapped[str | None] = mapped_column(Text)
    # Only institution staff can read evidence and institutional record identifiers.
    internal_note: Mapped[str | None] = mapped_column(Text)
    record_reference: Mapped[str | None] = mapped_column(String(100))
    submission_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
