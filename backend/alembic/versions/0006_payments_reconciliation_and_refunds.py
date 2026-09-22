"""payments reconciliation and refunds

Revision ID: 0006
Revises: 0005
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("active_order_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("account_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider_reference", sa.String(length=150), nullable=True),
        sa.Column("transaction_reference", sa.String(length=150), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=12), nullable=True),
        sa.Column("request_data", sa.JSON(), nullable=False),
        sa.Column("refunded_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_minor > 0", name="ck_payment_positive_amount"),
        sa.CheckConstraint(
            "refunded_minor >= 0 AND refunded_minor <= amount_minor",
            name="ck_payment_refund_total",
        ),
        sa.UniqueConstraint(
            "provider", "transaction_reference", name="uq_payment_transaction_reference"
        ),
        sa.ForeignKeyConstraint(
            ["active_order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_order_id"),
        sa.UniqueConstraint("order_id", "idempotency_key", name="uq_payment_order_key"),
        sa.UniqueConstraint(
            "provider", "provider_reference", name="uq_payment_provider_reference"
        ),
    )
    op.create_index(
        op.f("ix_payment_attempts_order_id"),
        "payment_attempts",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payment_attempts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_events_payment_id"),
        "payment_events",
        ["payment_id"],
        unique=False,
    )
    op.create_table(
        "payment_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("entry_key", sa.String(length=100), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payment_attempts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", "entry_key", name="uq_payment_ledger_entry"),
    )
    op.create_index(
        op.f("ix_payment_ledger_payment_id"),
        "payment_ledger",
        ["payment_id"],
        unique=False,
    )
    op.create_table(
        "payment_refunds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider_reference", sa.String(length=150), nullable=True),
        sa.Column("correlation_id", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payment_attempts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id"),
    )
    op.create_index(
        op.f("ix_payment_refunds_order_id"),
        "payment_refunds",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "payment_webhooks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("event_key", sa.String(length=150), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payment_attempts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_key", name="uq_payment_webhook_event"),
    )
    op.create_index(
        op.f("ix_payment_webhooks_payment_id"),
        "payment_webhooks",
        ["payment_id"],
        unique=False,
    )
    with op.batch_alter_table("order_events") as batch:
        batch.alter_column("actor_id", existing_type=sa.INTEGER(), nullable=True)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM order_events WHERE actor_id IS NULL")
    ):
        raise RuntimeError(
            "Cannot downgrade after provider events exist; preserve financial history and restore a reviewed backup instead."
        )
    with op.batch_alter_table("order_events") as batch:
        batch.alter_column("actor_id", existing_type=sa.INTEGER(), nullable=False)
    op.drop_index(op.f("ix_payment_webhooks_payment_id"), table_name="payment_webhooks")
    op.drop_table("payment_webhooks")
    op.drop_index(op.f("ix_payment_refunds_order_id"), table_name="payment_refunds")
    op.drop_table("payment_refunds")
    op.drop_index(op.f("ix_payment_ledger_payment_id"), table_name="payment_ledger")
    op.drop_table("payment_ledger")
    op.drop_index(op.f("ix_payment_events_payment_id"), table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index(op.f("ix_payment_attempts_order_id"), table_name="payment_attempts")
    op.drop_table("payment_attempts")
