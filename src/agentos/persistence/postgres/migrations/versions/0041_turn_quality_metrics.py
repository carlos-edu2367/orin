"""record per-turn tool efficiency so loop changes can be measured

Revision ID: 0041_turn_quality_metrics
Revises: 0040_execution_recovery_journal
"""
from alembic import op
import sqlalchemy as sa


revision = "0041_turn_quality_metrics"
down_revision = "0040_execution_recovery_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "turn_quality_metrics",
        sa.Column("turn_id", sa.String(255), primary_key=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("redundant_tool_calls", sa.Integer(), nullable=False),
        sa.Column("iterations", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        # Nullable on purpose: NULL is "the provider never reported cache
        # usage", which is not the same fact as a measured zero.
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_turn_quality_metrics_model",
        "turn_quality_metrics",
        ["user_id", "provider", "model_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_turn_quality_metrics_model", table_name="turn_quality_metrics")
    op.drop_table("turn_quality_metrics")
