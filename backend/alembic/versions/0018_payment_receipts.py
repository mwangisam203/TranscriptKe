"""Durable payment receipt email outbox."""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_receipt_emails",
        sa.Column(
            "payment_id",
            sa.String(36),
            sa.ForeignKey("payment_attempts.id"),
            primary_key=True,
        ),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_payment_receipt_emails_order_id", "payment_receipt_emails", ["order_id"]
    )
    op.create_index(
        "ix_payment_receipt_emails_sent_at", "payment_receipt_emails", ["sent_at"]
    )
    with op.batch_alter_table("worker_runs") as batch:
        batch.drop_constraint("ck_worker_name", type_="check")
        batch.create_check_constraint(
            "ck_worker_name", "worker IN ('payments','deliveries','receipts')"
        )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM payment_receipt_emails")):
        raise RuntimeError(
            "Payment receipts exist; preserve their payment audit history"
        )
    op.execute(sa.text("DELETE FROM worker_runs WHERE worker='receipts'"))
    with op.batch_alter_table("worker_runs") as batch:
        batch.drop_constraint("ck_worker_name", type_="check")
        batch.create_check_constraint(
            "ck_worker_name", "worker IN ('payments','deliveries')"
        )
    op.drop_table("payment_receipt_emails")
