"""Encrypted personal/contact details and optional enrollment status."""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("details_ciphertext", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "academic_record_links",
        sa.Column("currently_enrolled", sa.Boolean(), nullable=True),
    )


def downgrade():
    op.drop_column("academic_record_links", "currently_enrolled")
    op.drop_table("user_profiles")
