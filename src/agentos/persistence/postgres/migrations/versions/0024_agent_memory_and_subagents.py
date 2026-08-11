"""Durable agent memory and conversation subagents.

Revision ID: 0024_agent_memory
Revises: 0023_tool_invocations
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_agent_memory"
down_revision = "0023_tool_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("memory_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(255), nullable=True),
        sa.Column("fact", sa.String(2000), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "fact", name="uq_agent_memories_user_fact"),
    )
    op.create_index("ix_agent_memories_user_updated", "agent_memories", ["user_id", "updated_at"])
    op.create_table(
        "conversation_agents",
        sa.Column("agent_id", sa.String(255), primary_key=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("parent_agent_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(512), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "name", name="uq_conversation_agents_name"),
    )
    op.create_index("ix_conversation_agents_conversation", "conversation_agents", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_agents_conversation", table_name="conversation_agents")
    op.drop_table("conversation_agents")
    op.drop_index("ix_agent_memories_user_updated", table_name="agent_memories")
    op.drop_table("agent_memories")
