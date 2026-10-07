"""Signed consent, identity number type, and encrypted unfinished forms."""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "academic_record_links",
        sa.Column("id_number_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "order_consents", sa.Column("signature_ciphertext", sa.Text(), nullable=True)
    )
    op.create_table(
        "workspace_drafts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("details_ciphertext", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("workspace_drafts")
    op.drop_column("order_consents", "signature_ciphertext")
    op.drop_column("academic_record_links", "id_number_type")
