"""Preserve attendance years and add optional months without inventing historical dates."""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    for side in ("start", "end"):
        op.add_column(
            "academic_record_links",
            sa.Column(f"attendance_{side}_month", sa.Integer(), nullable=True),
        )


def downgrade():
    for side in ("end", "start"):
        op.drop_column("academic_record_links", f"attendance_{side}_month")
