"""authentication and institution access

Revision ID: 0002
Revises: 0001
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limits",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("window", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "access_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_access_events_institution_id"),
        "access_events",
        ["institution_id"],
        unique=False,
    )
    op.create_table(
        "action_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("membership_role", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_action_tokens_email"), "action_tokens", ["email"], unique=False
    )
    op.create_index(
        op.f("ix_action_tokens_purpose"), "action_tokens", ["purpose"], unique=False
    )
    op.create_index(
        op.f("ix_action_tokens_user_id"), "action_tokens", ["user_id"], unique=False
    )
    op.create_table(
        "institution_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "staff",
                "manager",
                name="membership_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "institution_id", "user_id", name="uq_membership_institution_user"
        ),
    )
    op.create_index(
        op.f("ix_institution_memberships_institution_id"),
        "institution_memberships",
        ["institution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_institution_memberships_user_id"),
        "institution_memberships",
        ["user_id"],
        unique=False,
    )
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )
    # The original registration flow marked all emails verified without proof.
    # Preserve accounts, but require mailbox verification after this upgrade.
    op.execute(sa.text("UPDATE users SET is_email_verified = false"))


def downgrade() -> None:
    op.drop_column("users", "token_version")
    op.drop_index(
        op.f("ix_institution_memberships_user_id"), table_name="institution_memberships"
    )
    op.drop_index(
        op.f("ix_institution_memberships_institution_id"),
        table_name="institution_memberships",
    )
    op.drop_table("institution_memberships")
    op.drop_index(op.f("ix_action_tokens_user_id"), table_name="action_tokens")
    op.drop_index(op.f("ix_action_tokens_purpose"), table_name="action_tokens")
    op.drop_index(op.f("ix_action_tokens_email"), table_name="action_tokens")
    op.drop_table("action_tokens")
    op.drop_index(op.f("ix_access_events_institution_id"), table_name="access_events")
    op.drop_table("access_events")
    op.drop_table("auth_rate_limits")
