"""institution services and academic record matching

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "institution_services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("document_type", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("fee_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("processing_days_min", sa.Integer(), nullable=False),
        sa.Column("processing_days_max", sa.Integer(), nullable=False),
        sa.Column("delivery_methods", sa.JSON(), nullable=False),
        sa.Column("required_fields", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("currency = 'KES'", name="ck_service_currency"),
        sa.CheckConstraint(
            "fee_minor >= 0 AND fee_minor <= 1000000000", name="ck_service_fee"
        ),
        sa.CheckConstraint(
            "processing_days_min >= 0 AND processing_days_max >= processing_days_min AND processing_days_max <= 365",
            name="ck_service_processing_days",
        ),
        sa.CheckConstraint("version >= 1", name="ck_service_version"),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "institution_id", "code", name="uq_service_institution_code"
        ),
    )
    op.create_index(
        op.f("ix_institution_services_institution_id"),
        "institution_services",
        ["institution_id"],
        unique=False,
    )
    op.create_table(
        "ordering_policies",
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("accepting_requests", sa.Boolean(), nullable=False),
        sa.Column("student_instructions", sa.Text(), nullable=False),
        sa.Column("required_fields", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_policy_version"),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.PrimaryKeyConstraint("institution_id"),
    )
    op.create_table(
        "academic_record_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("admission_number", sa.String(length=100), nullable=False),
        sa.Column("name_on_record", sa.String(length=255), nullable=False),
        sa.Column("program", sa.String(length=255), nullable=True),
        sa.Column("attendance_start_year", sa.Integer(), nullable=True),
        sa.Column("attendance_end_year", sa.Integer(), nullable=True),
        sa.Column("previous_names", sa.JSON(), nullable=False),
        sa.Column("requirements_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("student_message", sa.Text(), nullable=True),
        sa.Column("record_reference", sa.String(length=100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status != 'matched' OR record_reference IS NOT NULL",
            name="ck_matched_record_reference",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'needs_information', 'matched', 'rejected')",
            name="ck_link_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_link_version"),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["institution_services.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "institution_id",
            "admission_number",
            name="uq_link_user_institution_admission",
        ),
    )
    op.create_index(
        op.f("ix_academic_record_links_institution_id"),
        "academic_record_links",
        ["institution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_academic_record_links_user_id"),
        "academic_record_links",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_matched_institution_record",
        "academic_record_links",
        ["institution_id", "record_reference"],
        unique=True,
        postgresql_where=sa.text("status = 'matched'"),
        sqlite_where=sa.text("status = 'matched'"),
    )
    op.create_table(
        "record_match_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("student_message", sa.Text(), nullable=True),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("record_reference", sa.String(length=100), nullable=True),
        sa.Column("submission_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["link_id"],
            ["academic_record_links.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("link_id", "version", name="uq_match_event_link_version"),
    )
    op.create_index(
        op.f("ix_record_match_events_link_id"),
        "record_match_events",
        ["link_id"],
        unique=False,
    )
    op.add_column(
        "access_events",
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
    )
    op.add_column(
        "institutions",
        sa.Column("is_approved", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("institutions", "is_approved")
    op.drop_column("access_events", "details")
    op.drop_index(
        op.f("ix_record_match_events_link_id"), table_name="record_match_events"
    )
    op.drop_table("record_match_events")
    op.drop_index(
        "uq_matched_institution_record",
        table_name="academic_record_links",
        postgresql_where=sa.text("status = 'matched'"),
        sqlite_where=sa.text("status = 'matched'"),
    )
    op.drop_index(
        op.f("ix_academic_record_links_user_id"), table_name="academic_record_links"
    )
    op.drop_index(
        op.f("ix_academic_record_links_institution_id"),
        table_name="academic_record_links",
    )
    op.drop_table("academic_record_links")
    op.drop_table("ordering_policies")
    op.drop_index(
        op.f("ix_institution_services_institution_id"),
        table_name="institution_services",
    )
    op.drop_table("institution_services")
