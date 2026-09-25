"""Private onboarding and reviewed pilot evidence; preserves existing approvals."""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "institution_onboarding",
        sa.Column(
            "institution_id",
            sa.Integer(),
            sa.ForeignKey("institutions.id"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("review_reason", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_onboarding_version"),
        sa.CheckConstraint(
            "status IN ('draft','submitted','changes_requested','approved')",
            name="ck_onboarding_status",
        ),
    )
    op.create_table(
        "pilot_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "institution_id",
            sa.Integer(),
            sa.ForeignKey("institutions.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("findings", sa.Text(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("review_reason", sa.Text()),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_snapshot", sa.JSON()),
        sa.CheckConstraint("mode IN ('demo','live')", name="ck_pilot_mode"),
        sa.CheckConstraint(
            "decision IN ('pending','continue_pilot','rework','expand')",
            name="ck_pilot_decision",
        ),
        sa.CheckConstraint("window_end > window_start", name="ck_pilot_window"),
        sa.CheckConstraint("version >= 1", name="ck_pilot_version"),
    )
    op.create_index(
        "ix_pilot_evaluations_institution_id", "pilot_evaluations", ["institution_id"]
    )


def downgrade():
    op.drop_index("ix_pilot_evaluations_institution_id", table_name="pilot_evaluations")
    op.drop_table("pilot_evaluations")
    op.drop_table("institution_onboarding")
