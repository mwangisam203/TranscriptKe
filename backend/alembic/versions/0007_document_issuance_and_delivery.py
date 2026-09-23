"""Institution-prepared documents and secure recipient delivery."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "issued_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("active_item_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_by", sa.Integer(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["active_item_id"],
            ["order_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["issued_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["order_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_item_id"),
        sa.CheckConstraint("mode IN ('demo','live')", name="ck_document_mode"),
        sa.CheckConstraint("size BETWEEN 1 AND 2097152", name="ck_document_size"),
        sa.CheckConstraint(
            "active_item_id IS NULL OR active_item_id = item_id",
            name="ck_document_active_item",
        ),
    )
    op.create_index(
        op.f("ix_issued_documents_order_id"),
        "issued_documents",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "document_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "notification_attempted_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["issued_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
        sa.CheckConstraint("download_count >= 0", name="ck_delivery_download_count"),
        sa.CheckConstraint(
            "notification_attempts >= 0", name="ck_delivery_notification_attempts"
        ),
    )
    op.create_index(
        op.f("ix_document_deliveries_order_id"),
        "document_deliveries",
        ["order_id"],
        unique=False,
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM issued_documents")):
        raise RuntimeError(
            "Cannot discard document issuance history; restore a reviewed backup instead."
        )
    op.drop_index(
        op.f("ix_document_deliveries_order_id"), table_name="document_deliveries"
    )
    op.drop_table("document_deliveries")
    op.drop_index(op.f("ix_issued_documents_order_id"), table_name="issued_documents")
    op.drop_table("issued_documents")
