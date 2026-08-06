"""create RFC 601 persistence tables

Revision ID: 0001_initial_persistence
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persistence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("record_ref", sa.String(255), nullable=False),
        sa.Column("record_type", sa.String(96), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("record_ref", name="uq_persistence_records_record_ref"),
    )
    op.create_index(
        "ix_persistence_records_scope",
        "persistence_records",
        ["user_id", "workspace_id", "agent_id", "execution_id", "record_type"],
    )

    op.create_table(
        "persistence_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("audit_ref", sa.String(128), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("record_ref", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("audit_ref", name="uq_persistence_audit_ref"),
    )

    op.create_table(
        "persistence_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("source_record_ref", sa.String(255), nullable=False),
        sa.Column("expected_source_version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(128), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("event", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", name="uq_persistence_outbox_event_id"),
    )
    op.create_index("ix_persistence_outbox_pending", "persistence_outbox", ["published_at", "created_at"])
    op.create_index("ix_persistence_outbox_scope", "persistence_outbox", ["user_id", "workspace_id", "execution_id"])

    op.create_table(
        "persistence_idempotency",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("commit_state", sa.String(32), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("store_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "agent_id", "execution_id", "purpose", "idempotency_key",
            name="uq_persistence_idempotency_scope",
        ),
    )
    op.create_index("ix_persistence_idempotency_transaction", "persistence_idempotency", ["transaction_id", "user_id", "execution_id"])


def downgrade() -> None:
    op.drop_index("ix_persistence_idempotency_transaction", table_name="persistence_idempotency")
    op.drop_table("persistence_idempotency")
    op.drop_index("ix_persistence_outbox_scope", table_name="persistence_outbox")
    op.drop_index("ix_persistence_outbox_pending", table_name="persistence_outbox")
    op.drop_table("persistence_outbox")
    op.drop_table("persistence_audit")
    op.drop_index("ix_persistence_records_scope", table_name="persistence_records")
    op.drop_table("persistence_records")
