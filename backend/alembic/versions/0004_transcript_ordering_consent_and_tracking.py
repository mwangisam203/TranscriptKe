"""transcript ordering consent and tracking

Revision ID: 0004
Revises: 0003
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("academic_record_link_id", sa.Integer(), nullable=False),
        sa.Column("creation_key", sa.String(length=100), nullable=False),
        sa.Column("creation_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=1000), nullable=False),
        sa.Column("release_when", sa.String(length=30), nullable=False),
        sa.Column("release_instruction", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("payment_status", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("submitted_snapshot", sa.JSON(), nullable=True),
        sa.Column("submission_key", sa.String(length=100), nullable=True),
        sa.Column("submission_quote_id", sa.String(length=36), nullable=True),
        sa.Column("submission_consent_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','submitted','cancellation_requested','cancelled')",
            name="ck_order_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_order_version"),
        sa.ForeignKeyConstraint(
            ["academic_record_link_id"],
            ["academic_record_links.id"],
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
        sa.UniqueConstraint(
            "user_id", "creation_key", name="uq_order_user_creation_key"
        ),
    )
    op.create_index(
        op.f("ix_orders_institution_id"), "orders", ["institution_id"], unique=False
    )
    op.create_index(op.f("ix_orders_user_id"), "orders", ["user_id"], unique=False)
    op.create_table(
        "order_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("scan_method", sa.String(length=30), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_attachments_order_id"),
        "order_attachments",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "order_cancellations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_cancellations_order_id"),
        "order_cancellations",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "version", name="uq_order_event_version"),
    )
    op.create_index(
        op.f("ix_order_events_order_id"), "order_events", ["order_id"], unique=False
    )
    op.create_table(
        "order_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("author_role", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("requires_response", sa.Boolean(), nullable=False),
        sa.Column("in_reply_to_id", sa.Integer(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["in_reply_to_id"],
            ["order_messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_messages_order_id"), "order_messages", ["order_id"], unique=False
    )
    op.create_table(
        "order_quotes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("order_version", sa.Integer(), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_quotes_order_id"), "order_quotes", ["order_id"], unique=False
    )
    op.create_table(
        "order_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("organization", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("delivery_method", sa.String(length=30), nullable=False),
        sa.Column("postal_address", sa.JSON(), nullable=True),
        sa.Column("application_reference", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "key", name="uq_order_recipient_key"),
    )
    op.create_index(
        op.f("ix_order_recipients_order_id"),
        "order_recipients",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "order_consents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text_version", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["order_quotes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id"),
    )
    op.create_index(
        op.f("ix_order_consents_order_id"), "order_consents", ["order_id"], unique=False
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("recipient_key", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fulfillment_status", sa.String(length=30), nullable=False),
        sa.CheckConstraint("quantity BETWEEN 1 AND 10", name="ck_order_item_quantity"),
        sa.ForeignKeyConstraint(
            ["order_id", "recipient_key"],
            ["order_recipients.order_id", "order_recipients.key"],
            name="fk_item_order_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["institution_services.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "key", name="uq_order_item_key"),
        sa.UniqueConstraint(
            "order_id", "service_id", "recipient_key", name="uq_order_service_recipient"
        ),
    )
    op.create_index(
        op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_index(op.f("ix_order_consents_order_id"), table_name="order_consents")
    op.drop_table("order_consents")
    op.drop_index(op.f("ix_order_recipients_order_id"), table_name="order_recipients")
    op.drop_table("order_recipients")
    op.drop_index(op.f("ix_order_quotes_order_id"), table_name="order_quotes")
    op.drop_table("order_quotes")
    op.drop_index(op.f("ix_order_messages_order_id"), table_name="order_messages")
    op.drop_table("order_messages")
    op.drop_index(op.f("ix_order_events_order_id"), table_name="order_events")
    op.drop_table("order_events")
    op.drop_index(
        op.f("ix_order_cancellations_order_id"), table_name="order_cancellations"
    )
    op.drop_table("order_cancellations")
    op.drop_index(op.f("ix_order_attachments_order_id"), table_name="order_attachments")
    op.drop_table("order_attachments")
    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_institution_id"), table_name="orders")
    op.drop_table("orders")
