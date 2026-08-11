"""persist projects and their shared workspace membership.

Revision ID: 0028_projects
Revises: 0027_agent_skills
"""
from alembic import op
import sqlalchemy as sa


revision = "0028_projects"
down_revision = "0027_agent_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_projects_user_active", "projects", ["user_id", "archived_at", "updated_at"])
    op.add_column("conversations", sa.Column("project_id", sa.String(255), nullable=True))
    op.create_index("ix_conversations_project_updated", "conversations", ["project_id", "updated_at"])
    op.add_column("agent_memories", sa.Column("scope_type", sa.String(16), nullable=False, server_default="user"))
    op.add_column("agent_memories", sa.Column("scope_id", sa.String(255), nullable=False, server_default=""))
    op.add_column("agent_memories", sa.Column("project_id", sa.String(255), nullable=True))
    op.add_column("agent_memories", sa.Column("source_message_id", sa.String(255), nullable=True))
    op.add_column("agent_memories", sa.Column("source_execution_id", sa.String(255), nullable=True))
    op.execute("UPDATE agent_memories SET scope_id = user_id WHERE scope_id = ''")
    op.create_index("ix_agent_memories_scope", "agent_memories", ["user_id", "scope_type", "project_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_scope", table_name="agent_memories")
    op.drop_column("agent_memories", "source_execution_id")
    op.drop_column("agent_memories", "source_message_id")
    op.drop_column("agent_memories", "project_id")
    op.drop_column("agent_memories", "scope_id")
    op.drop_column("agent_memories", "scope_type")
    op.drop_index("ix_conversations_project_updated", table_name="conversations")
    op.drop_column("conversations", "project_id")
    op.drop_index("ix_projects_user_active", table_name="projects")
    op.drop_table("projects")
