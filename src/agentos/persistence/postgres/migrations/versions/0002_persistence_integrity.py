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
    op.execute(
        sa.text(
            "INSERT INTO persistence_clock (id, revision) "
            "SELECT 1, COALESCE(MAX(store_revision), 0) FROM persistence_idempotency"
        )
    )

    with op.batch_alter_table("persistence_records", recreate="always") as batch:
        batch.add_column(
            sa.Column("workspace_scope", sa.String(255), nullable=False, server_default=sa.text("''"))
        )
        batch.create_check_constraint("ck_persistence_records_version_positive", "version > 0")
        batch.create_check_constraint(
            "ck_persistence_records_classification",
            "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        )
        batch.create_unique_constraint(
            "uq_persistence_records_ownership",
            ["record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
        )

    op.execute(sa.text("UPDATE persistence_records SET workspace_scope = COALESCE(workspace_id, '')"))

    with op.batch_alter_table("persistence_audit", recreate="always") as batch:
        batch.add_column(
            sa.Column("workspace_scope", sa.String(255), nullable=False, server_default=sa.text("''"))
        )
        batch.create_check_constraint("ck_persistence_audit_version_positive", "resulting_version > 0")

    op.execute(sa.text("UPDATE persistence_audit SET workspace_scope = COALESCE(workspace_id, '')"))

    with op.batch_alter_table("persistence_audit", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_persistence_audit_record_scope",
            "persistence_records",
            ["record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
            ["record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
        )

    with op.batch_alter_table("persistence_outbox", recreate="always") as batch:
        batch.add_column(
            sa.Column("workspace_scope", sa.String(255), nullable=False, server_default=sa.text("''"))
        )
        batch.add_column(
            sa.Column("actor", sa.String(255), nullable=False, server_default=sa.text("''"))
        )
        batch.create_check_constraint("ck_persistence_outbox_version_positive", "expected_source_version > 0")
        batch.create_check_constraint(
            "ck_persistence_outbox_classification",
            "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        )

    op.execute(
        sa.text(
            "UPDATE persistence_outbox SET workspace_scope = COALESCE(workspace_id, ''), "
            "actor = COALESCE((SELECT actor FROM persistence_records "
            "WHERE persistence_records.record_ref = persistence_outbox.source_record_ref), '')"
        )
    )

    with op.batch_alter_table("persistence_outbox", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_persistence_outbox_source_scope",
            "persistence_records",
            ["source_record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
            ["record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
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
            "correlation_id = '__legacy__:' || CAST(id AS VARCHAR(32)) "
            "WHERE correlation_id = ''"
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
        batch.drop_constraint("ck_persistence_idempotency_workspace_scope", type_="check")
        batch.drop_constraint("ck_persistence_idempotency_commit_state", type_="check")
        batch.drop_constraint("uq_persistence_idempotency_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_persistence_idempotency_scope",
            ["user_id", "workspace_id", "agent_id", "execution_id", "purpose", "actor", "idempotency_key"],
        )
        batch.drop_column("records")
        batch.drop_column("correlation_id")
        batch.drop_column("workspace_scope")

    with op.batch_alter_table("persistence_outbox", recreate="always") as batch:
        batch.drop_constraint("fk_persistence_outbox_source_scope", type_="foreignkey")
        batch.drop_constraint("ck_persistence_outbox_classification", type_="check")
        batch.drop_constraint("ck_persistence_outbox_version_positive", type_="check")
        batch.drop_column("workspace_scope")
        batch.drop_column("actor")

    with op.batch_alter_table("persistence_audit", recreate="always") as batch:
        batch.drop_constraint("fk_persistence_audit_record_scope", type_="foreignkey")
        batch.drop_constraint("ck_persistence_audit_version_positive", type_="check")
        batch.drop_column("workspace_scope")

    with op.batch_alter_table("persistence_records", recreate="always") as batch:
        batch.drop_constraint("uq_persistence_records_ownership", type_="unique")
        batch.drop_constraint("ck_persistence_records_classification", type_="check")
        batch.drop_constraint("ck_persistence_records_version_positive", type_="check")
        batch.drop_column("workspace_scope")
