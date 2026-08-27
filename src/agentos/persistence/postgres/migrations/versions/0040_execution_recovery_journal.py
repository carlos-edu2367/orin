"""add durable execution checkpoints and external-effect journal

Revision ID: 0040_execution_recovery_journal
Revises: 0039_custom_provider_models
"""
from alembic import op
import sqlalchemy as sa


revision = "0040_execution_recovery_journal"
down_revision = "0039_custom_provider_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_checkpoints",
        sa.Column("checkpoint_id", sa.String(255), primary_key=True),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("execution_state_version", sa.Integer(), nullable=False),
        sa.Column("context_manifest_ref", sa.String(255), nullable=False),
        sa.Column("next_decision", sa.String(64), nullable=False),
        sa.Column("pending_effect_id", sa.String(255), nullable=True),
        sa.Column("is_safe", sa.Boolean(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "sequence", name="uq_execution_checkpoints_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_execution_checkpoints_sequence_positive"),
    )
    op.create_index("ix_execution_checkpoints_latest", "execution_checkpoints", ["execution_id", "sequence"])
    op.create_table(
        "execution_effects",
        sa.Column("effect_id", sa.String(255), primary_key=True),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("invocation_ref", sa.String(255), nullable=False),
        sa.Column("request_ref", sa.String(255), nullable=False),
        sa.Column("result_ref", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("retryability", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_ref", sa.String(255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("execution_id", "idempotency_key", name="uq_execution_effects_idempotency"),
        sa.CheckConstraint("attempt > 0", name="ck_execution_effects_attempt_positive"),
        sa.CheckConstraint("version > 0", name="ck_execution_effects_version_positive"),
    )
    op.create_index("ix_execution_effects_recovery", "execution_effects", ["execution_id", "state", "prepared_at"])


def downgrade() -> None:
    op.drop_index("ix_execution_effects_recovery", table_name="execution_effects")
    op.drop_table("execution_effects")
    op.drop_index("ix_execution_checkpoints_latest", table_name="execution_checkpoints")
    op.drop_table("execution_checkpoints")
