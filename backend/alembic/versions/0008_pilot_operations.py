"""Pilot operations follow-ups, planning targets and worker health."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _planning_target(submitted_at, snapshot):
    if not submitted_at or not isinstance(snapshot, dict):
        return None
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        return None
    values = [
        item.get("processing_days_max") if isinstance(item, dict) else None
        for item in items
    ]
    if any(type(value) is not int or not 0 <= value <= 365 for value in values):
        return None
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    zone = ZoneInfo("Africa/Nairobi")
    day = submitted_at.astimezone(zone).date()
    remaining = max(values)
    while remaining:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return datetime.combine(day, time(23, 59, 59), tzinfo=zone).astimezone(timezone.utc)


def upgrade():
    op.create_table(
        "worker_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("worker", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed')", name="ck_worker_status"
        ),
        sa.CheckConstraint(
            "worker IN ('payments','deliveries')", name="ck_worker_name"
        ),
        sa.CheckConstraint("processed >= 0 AND failed >= 0", name="ck_worker_counts"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_worker_runs_started_at"), "worker_runs", ["started_at"], unique=False
    )
    op.create_index(
        op.f("ix_worker_runs_worker"), "worker_runs", ["worker"], unique=False
    )
    op.create_table(
        "operations_cases",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index(
        op.f("ix_operations_cases_follow_up_at"),
        "operations_cases",
        ["follow_up_at"],
        unique=False,
    )
    op.add_column(
        "orders",
        sa.Column("processing_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_orders_processing_due_at"),
        "orders",
        ["processing_due_at"],
        unique=False,
    )
    orders = sa.table(
        "orders",
        sa.column("id", sa.Integer()),
        sa.column("submitted_at", sa.DateTime(timezone=True)),
        sa.column("submitted_snapshot", sa.JSON()),
        sa.column("processing_due_at", sa.DateTime(timezone=True)),
    )
    connection = op.get_bind()
    last = 0
    while True:
        batch = (
            connection.execute(
                sa.select(
                    orders.c.id, orders.c.submitted_at, orders.c.submitted_snapshot
                )
                .where(orders.c.id > last, orders.c.submitted_at.is_not(None))
                .order_by(orders.c.id)
                .limit(500)
            )
            .mappings()
            .all()
        )
        if not batch:
            break
        for row in batch:
            due = _planning_target(row["submitted_at"], row["submitted_snapshot"])
            connection.execute(
                orders.update()
                .where(orders.c.id == row["id"])
                .values(processing_due_at=due)
            )
        last = batch[-1]["id"]


def downgrade():
    op.drop_index(op.f("ix_orders_processing_due_at"), table_name="orders")
    op.drop_column("orders", "processing_due_at")
    op.drop_index(
        op.f("ix_operations_cases_follow_up_at"), table_name="operations_cases"
    )
    op.drop_table("operations_cases")
    op.drop_index(op.f("ix_worker_runs_worker"), table_name="worker_runs")
    op.drop_index(op.f("ix_worker_runs_started_at"), table_name="worker_runs")
    op.drop_table("worker_runs")
