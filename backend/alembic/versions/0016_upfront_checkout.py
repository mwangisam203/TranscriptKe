"""Private upfront checkout; release orders only after confirmed payment."""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "orders",
        sa.Column(
            "collection_policy",
            sa.String(20),
            nullable=False,
            server_default="after_review",
        ),
    )
    op.add_column(
        "academic_record_links",
        sa.Column(
            "checkout_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    change_status_constraint(
        "'draft','awaiting_payment','submitted','cancellation_requested','cancelled'"
    )


def change_status_constraint(states, *, drop_policy=False):
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    # SQLite rebuilds the parent table for CHECK changes. Defer reference checks
    # during that rebuild, then verify the replacement has preserved all children.
    # PostgreSQL changes the constraint in place and remains transactional.
    if sqlite:
        # pysqlite otherwise auto-commits CREATE TABLE before the batch's INSERT,
        # which resets defer_foreign_keys and makes DROP of this parent fail.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    try:
        with op.batch_alter_table("orders") as batch:
            batch.drop_constraint("ck_order_status", type_="check")
            batch.create_check_constraint("ck_order_status", f"status IN ({states})")
            if drop_policy:
                batch.drop_column("collection_policy")
        if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise RuntimeError("Foreign key validation failed after checkout migration")
    finally:
        if sqlite:
            connection.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT COUNT(*) FROM orders WHERE collection_policy = 'before_review'")
    ):
        raise RuntimeError(
            "Upfront checkout orders exist; preserve their payment policy and audit history."
        )
    if connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM academic_record_links WHERE checkout_required = true"
        )
    ):
        raise RuntimeError(
            "Private enrollment records exist; preserve their checkout access restriction."
        )
    change_status_constraint(
        "'draft','submitted','cancellation_requested','cancelled'", drop_policy=True
    )
    op.drop_column("academic_record_links", "checkout_required")
