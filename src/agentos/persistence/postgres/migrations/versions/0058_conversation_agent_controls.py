"""User controls (stop and direct messages) addressed to a running subagent

Revision ID: 0058_conversation_agent_controls
Revises: 0057_user_agents
"""
from alembic import op
import sqlalchemy as sa


revision = "0058_conversation_agent_controls"
down_revision = "0057_user_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "conversation_agent_controls" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "conversation_agent_controls",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("control_id", sa.String(length=255), nullable=False, unique=True),
            sa.Column("conversation_id", sa.String(length=255), nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("agent_id", sa.String(length=255), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(length=16), nullable=True),
        )
    if "ix_conversation_agent_controls_pending" not in {
        index["name"] for index in sa.inspect(bind).get_indexes("conversation_agent_controls")
    }:
        op.create_index(
            "ix_conversation_agent_controls_pending",
            "conversation_agent_controls",
            ["conversation_id", "agent_id", "consumed_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "conversation_agent_controls" in set(sa.inspect(bind).get_table_names()):
        if "ix_conversation_agent_controls_pending" in {
            index["name"] for index in sa.inspect(bind).get_indexes("conversation_agent_controls")
        }:
            op.drop_index("ix_conversation_agent_controls_pending", table_name="conversation_agent_controls")
        op.drop_table("conversation_agent_controls")
