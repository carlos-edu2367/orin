"""Persist bounded collaboration and delegation projections."""
from alembic import op
import sqlalchemy as sa

revision = "0021_agentic_collaboration"
down_revision = "0020_browser_runs"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "agentic_collaboration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fact_id", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("conversation_id", sa.String(255)),
        sa.Column("parent_agent_id", sa.String(255), nullable=False),
        sa.Column("child_agent_id", sa.String(255)),
        sa.Column("parent_execution_id", sa.String(255), nullable=False),
        sa.Column("child_execution_id", sa.String(255)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agentic_collaboration_owner_conversation", "agentic_collaboration", ["user_id", "conversation_id", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_agentic_collaboration_owner_conversation", table_name="agentic_collaboration")
    op.drop_table("agentic_collaboration")
