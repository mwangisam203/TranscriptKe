"""Private encrypted front and back identity images for academic record matching."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "record_identity_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "link_id",
            sa.Integer(),
            sa.ForeignKey("academic_record_links.id"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(30), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("link_id", "side", name="uq_identity_image_side"),
        sa.CheckConstraint("side IN ('front', 'back')", name="ck_identity_image_side"),
        sa.CheckConstraint(
            "document_type IN ('national_id', 'driving_licence')",
            name="ck_identity_image_type",
        ),
    )
    op.create_index(
        "ix_record_identity_images_link_id", "record_identity_images", ["link_id"]
    )


def downgrade():
    op.drop_table("record_identity_images")
