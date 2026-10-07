"""Remember self and institution destinations while preserving existing recipients."""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite supports adding this column and its CHECK without rebuilding referenced tables.
    op.add_column(
        "order_recipients",
        sa.Column(
            "destination_type",
            sa.String(20),
            sa.CheckConstraint(
                "destination_type IN ('self', 'institution', 'other')",
                name="ck_recipient_destination_type",
            ),
            nullable=False,
            server_default="other",
        ),
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        # SQLite drops the inline column constraint with the column.
        op.execute("ALTER TABLE order_recipients DROP COLUMN destination_type")
    else:
        op.drop_constraint(
            "ck_recipient_destination_type", "order_recipients", type_="check"
        )
        op.drop_column("order_recipients", "destination_type")
