"""Expand agent_memories.fact to 4000 chars and support episodic memories in user/project scopes

Revision ID: 0059_memory_fact_4000_and_episodic
Revises: 0058_conversation_agent_controls
"""
from alembic import op
import sqlalchemy as sa


revision = "0059_memory_fact_4000_and_episodic"
down_revision = "0058_conversation_agent_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_memories" in set(inspector.get_table_names()):
        cols = {col["name"] for col in inspector.get_columns("agent_memories")}
        if "fact" in cols:
            with op.batch_alter_table("agent_memories") as batch:
                batch.alter_column(
                    "fact",
                    existing_type=sa.String(length=2000),
                    type_=sa.String(length=4000),
                    existing_nullable=False,
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_memories" in set(inspector.get_table_names()):
        cols = {col["name"] for col in inspector.get_columns("agent_memories")}
        if "fact" in cols:
            op.execute(
                sa.text(
                    "DELETE FROM agent_memories "
                    "WHERE LENGTH(fact) > 2000 AND EXISTS ("
                    "  SELECT 1 FROM agent_memories AS other "
                    "  WHERE other.memory_id <> agent_memories.memory_id "
                    "    AND other.user_id = agent_memories.user_id "
                    "    AND other.scope_type = agent_memories.scope_type "
                    "    AND COALESCE(other.project_id, '') = COALESCE(agent_memories.project_id, '') "
                    "    AND SUBSTR(other.fact, 1, 2000) = SUBSTR(agent_memories.fact, 1, 2000) "
                    "    AND (LENGTH(other.fact) <= 2000 OR other.memory_id < agent_memories.memory_id)"
                    ")"
                )
            )
            op.execute(
                sa.text(
                    "UPDATE agent_memories SET fact = SUBSTR(fact, 1, 2000) WHERE LENGTH(fact) > 2000"
                )
            )
            with op.batch_alter_table("agent_memories") as batch:
                batch.alter_column(
                    "fact",
                    existing_type=sa.String(length=4000),
                    type_=sa.String(length=2000),
                    existing_nullable=False,
                )
