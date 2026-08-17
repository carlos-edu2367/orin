"""persist plugin command expansions and SessionStart hook context

Revision ID: 0037_plugin_commands_and_hooks
Revises: 0036_oauth_tokens
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_plugin_commands_and_hooks"
down_revision = "0036_oauth_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_message_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("plugin_id", sa.String(64), nullable=False),
        sa.Column("command_id", sa.String(255), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("expanded_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_conversation_message_commands_conversation",
        "conversation_message_commands", ["conversation_id"],
    )
    op.create_table(
        "conversation_hook_context",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("plugin_id", sa.String(64), nullable=False),
        sa.Column("hook_id", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "hook_id", name="uq_conversation_hook_context"),
    )


def downgrade() -> None:
    op.drop_table("conversation_hook_context")
    op.drop_index(
        "ix_conversation_message_commands_conversation",
        table_name="conversation_message_commands",
    )
    op.drop_table("conversation_message_commands")
