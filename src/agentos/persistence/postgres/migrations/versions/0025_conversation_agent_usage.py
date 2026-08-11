"""Persist subagent model snapshots and token usage.

Revision ID: 0025_agent_usage
Revises: 0024_agent_memory
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_agent_usage"
down_revision = "0024_agent_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_agents", sa.Column("provider", sa.String(32), nullable=True))
    op.add_column("conversation_agents", sa.Column("model_id", sa.String(512), nullable=True))
    op.create_table(
        "conversation_agent_usage",
        sa.Column("conversation_id", sa.String(255), primary_key=True),
        sa.Column("agent_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("usage_reported", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_agent_usage_conversation", "conversation_agent_usage", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_agent_usage_conversation", table_name="conversation_agent_usage")
    op.drop_table("conversation_agent_usage")
    op.drop_column("conversation_agents", "model_id")
    op.drop_column("conversation_agents", "provider")
