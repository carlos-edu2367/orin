"""persist a user-chosen local folder as a workspace root.

Revision ID: 0030_workspace_roots
Revises: 0029_conversation_tool_records
"""
from alembic import op
import sqlalchemy as sa


revision = "0030_workspace_roots"
down_revision = "0029_conversation_tool_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_roots",
        sa.Column("workspace_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("root_path", sa.String(4096), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_roots_user", "workspace_roots", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_roots_user", table_name="workspace_roots")
    op.drop_table("workspace_roots")
