"""complete persistence scope and replay integrity

Revision ID: 0002_persistence_integrity
Revises: 0001_initial_persistence
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_persistence_integrity"
down_revision = "0001_initial_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persistence_clock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_persistence_clock_singleton"),
        sa.CheckConstraint("revision >= 0", name="ck_persistence_clock_revision_nonnegative"),
    )
    op.execute(sa.text("INSERT INTO persistence_clock (id, revision) VALUES (1, 0)"))

    with op.batch_alter_table("persistence_records", recreate="always") as batch:
        batch.create_check_constraint("ck_persistence_records_version_positive", "version > 0")

    with op.batch_alter_table("persistence_audit", recreate="always") as batch:
        batch.create_check_constraint("ck_persistence_audit_version_positive", "resulting_version > 0")
        batch.create_foreign_key(
            "fk_persistence_audit_record",
            "persistence_records",
            ["record_ref"],
            ["record_ref"],
        )

    with op.batch_alter_table("persistence_outbox", recreate="always") as batch:
        batch.create_check_constraint("ck_persistence_outbox_version_positive", "expected_source_version > 0")
        batch.create_check_constraint(
            "ck_persistence_outbox_classification",
            "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        )
        batch.create_foreign_key(
            "fk_persistence_outbox_source_record",
            "persistence_records",
            ["source_record_ref"],
            ["record_ref"],
        )

    with op.batch_alter_table("persistence_idempotency", recreate="always") as batch:
        batch.add_column(
            sa.Column("workspace_scope", sa.String(255), nullable=False, server_default=sa.text("''"))
        )
        batch.add_column(
            sa.Column("correlation_id", sa.String(255), nullable=False, server_default=sa.text("''"))
        )
        batch.add_column(
            sa.Column("records", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )

    op.execute(
        sa.text(
            "UPDATE persistence_idempotency "
            "SET workspace_scope = COALESCE(workspace_id, ''), "
            "correlation_id = '__legacy__' "
            "WHERE correlation_id = ''"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM persistence_idempotency AS older "
            "WHERE EXISTS ("
            "SELECT 1 FROM persistence_idempotency AS newer "
            "WHERE newer.id > older.id "
            "AND newer.user_id = older.user_id "
            "AND (newer.workspace_scope = older.workspace_scope) "
            "AND newer.agent_id = older.agent_id "
            "AND newer.execution_id = older.execution_id "
            "AND newer.purpose = older.purpose "
            "AND newer.actor = older.actor "
            "AND newer.idempotency_key = older.idempotency_key "
            "AND newer.correlation_id = '__legacy__'"
            ")"
        )
    )

    with op.batch_alter_table("persistence_idempotency", recreate="always") as batch:
        batch.drop_constraint("uq_persistence_idempotency_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_persistence_idempotency_scope",
            [
                "user_id",
                "workspace_scope",
                "agent_id",
                "execution_id",
                "correlation_id",
                "purpose",
                "actor",
                "idempotency_key",
            ],
        )
        batch.create_check_constraint("ck_persistence_idempotency_revision_nonnegative", "store_revision >= 0")
        batch.create_check_constraint(
            "ck_persistence_idempotency_workspace_scope",
            "workspace_scope = COALESCE(workspace_id, '')",
        )
        batch.create_check_constraint(
            "ck_persistence_idempotency_commit_state",
            "commit_state IN ('COMMITTED', 'NOT_COMMITTED', 'UNKNOWN')",
        )

def downgrade() -> None:
    op.drop_table("persistence_clock")

    with op.batch_alter_table("persistence_idempotency", recreate="always") as batch:
        batch.drop_constraint("ck_persistence_idempotency_revision_nonnegative", type_="check")
        batch.drop_constraint("uq_persistence_idempotency_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_persistence_idempotency_scope",
            ["user_id", "workspace_id", "agent_id", "execution_id", "purpose", "actor", "idempotency_key"],
        )
        batch.drop_column("records")
        batch.drop_column("correlation_id")
        batch.drop_column("workspace_scope")

    with op.batch_alter_table("persistence_outbox", recreate="always") as batch:
        batch.drop_constraint("fk_persistence_outbox_source_record", type_="foreignkey")
        batch.drop_constraint("ck_persistence_outbox_classification", type_="check")
        batch.drop_constraint("ck_persistence_outbox_version_positive", type_="check")

    with op.batch_alter_table("persistence_audit", recreate="always") as batch:
        batch.drop_constraint("fk_persistence_audit_record", type_="foreignkey")
        batch.drop_constraint("ck_persistence_audit_version_positive", type_="check")

    with op.batch_alter_table("persistence_records", recreate="always") as batch:
        batch.drop_constraint("ck_persistence_records_version_positive", type_="check")
