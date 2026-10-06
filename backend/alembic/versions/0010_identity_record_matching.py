"""Encrypted ID fallback for record matching; preserve existing admission links."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


@contextmanager
def identity_batch():
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    deferred = 0
    if sqlite:
        # sqlite3's legacy mode does not start transactions for SELECT/DDL.
        # Begin explicitly so DDL cannot reset the deferral before the copy.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        # All references to this table use NO ACTION, not cascading deletes.
        # Defer the temporary missing-parent check while Alembic replaces the table.
        # https://www.sqlite.org/pragma.html#pragma_defer_foreign_keys
        deferred = connection.exec_driver_sql("PRAGMA defer_foreign_keys").scalar()
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise RuntimeError(
                "Resolve existing foreign key violations before migrating"
            )
        connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    with op.batch_alter_table("academic_record_links") as batch:
        yield batch
    if sqlite:
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise RuntimeError(
                "Identity migration failed its foreign key integrity check"
            )
        # Reset obsolete deferred checks only after confirming every reference is valid.
        connection.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")
        if deferred:
            connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")


def upgrade():
    with identity_batch() as batch:
        batch.alter_column(
            "admission_number", existing_type=sa.String(100), nullable=True
        )
        batch.add_column(sa.Column("identity_ciphertext", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("identity_fingerprint", sa.String(64), nullable=True)
        )
        batch.add_column(sa.Column("identity_masked", sa.String(10), nullable=True))
        batch.create_unique_constraint(
            "uq_link_user_institution_identity",
            ["user_id", "institution_id", "identity_fingerprint"],
        )
        batch.create_check_constraint(
            "ck_link_identifier",
            "admission_number IS NOT NULL OR identity_ciphertext IS NOT NULL",
        )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT count(*) FROM academic_record_links WHERE admission_number IS NULL"
        )
    ):
        raise RuntimeError(
            "ID-only links must receive verified admission numbers before downgrading; no records were deleted"
        )
    with identity_batch() as batch:
        batch.drop_constraint("ck_link_identifier", type_="check")
        batch.drop_constraint("uq_link_user_institution_identity", type_="unique")
        batch.drop_column("identity_masked")
        batch.drop_column("identity_fingerprint")
        batch.drop_column("identity_ciphertext")
        batch.alter_column(
            "admission_number", existing_type=sa.String(100), nullable=False
        )
