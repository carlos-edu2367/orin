"""typed agent memory with provenance and supersession

Revision ID: 0044_memory_learning
Revises: 0043_code_mode
"""
from alembic import op
import sqlalchemy as sa


revision = "0044_memory_learning"
down_revision = "0043_code_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_memories", sa.Column("kind", sa.String(16), nullable=False, server_default="fact"))
    op.add_column("agent_memories", sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("agent_memories", sa.Column("source", sa.String(16), nullable=False, server_default="user_explicit"))
    op.add_column("agent_memories", sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_memories", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_memories", sa.Column("superseded_by", sa.String(255), nullable=True))
    # Every row that predates this migration was written by the model calling
    # `remember` on the user's behalf, which is exactly user_explicit/fact.
    with op.batch_alter_table("agent_memories") as batch:
        batch.drop_constraint("uq_agent_memories_user_fact", type_="unique")
        batch.create_unique_constraint(
            "uq_agent_memories_scope_fact", ["user_id", "scope_type", "project_id", "fact"]
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_memories") as batch:
        batch.drop_constraint("uq_agent_memories_scope_fact", type_="unique")
        batch.create_unique_constraint("uq_agent_memories_user_fact", ["user_id", "fact"])
    op.drop_column("agent_memories", "superseded_by")
    op.drop_column("agent_memories", "last_used_at")
    op.drop_column("agent_memories", "hit_count")
    op.drop_column("agent_memories", "source")
    op.drop_column("agent_memories", "confidence")
    op.drop_column("agent_memories", "kind")
