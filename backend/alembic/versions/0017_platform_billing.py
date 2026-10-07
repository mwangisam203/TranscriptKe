"""Platform merchant routing with school-level collection controls."""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "institution_billing",
        sa.Column(
            "institution_id",
            sa.Integer(),
            sa.ForeignKey("institutions.id"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "payment_attempts",
        sa.Column(
            "merchant_scope",
            sa.String(20),
            nullable=False,
            server_default="institution",
        ),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM payment_attempts WHERE merchant_scope='platform'")
    ):
        raise RuntimeError(
            "Platform payments exist; preserve their refund authority and audit history"
        )
    op.drop_column("payment_attempts", "merchant_scope")
    op.drop_table("institution_billing")
