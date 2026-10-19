"""W5: the mailbox of agent teams

Revision ID: 0050_w5_extensibility
Revises: 0049_w4_tools
"""
from alembic import op
import sqlalchemy as sa


revision = "0050_w5_extensibility"
down_revision = "0049_w4_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AgentMailbox creates the table itself on a database that skipped migrations: create it only when it is missing.
    bind = op.get_bind()
    if "agent_mailbox" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "agent_mailbox",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("message_id", sa.String(255), nullable=False, unique=True),
            sa.Column("conversation_id", sa.String(255), nullable=False),
            sa.Column("user_id", sa.String(255), nullable=False),
            sa.Column("team_name", sa.String(64), nullable=False),
            sa.Column("sender", sa.String(64), nullable=False),
            sa.Column("recipient", sa.String(64), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("summary", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "ix_agent_mailbox_pending" not in {index["name"] for index in sa.inspect(bind).get_indexes("agent_mailbox")}:
        op.create_index("ix_agent_mailbox_pending", "agent_mailbox", ["conversation_id", "recipient", "delivered_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_mailbox_pending", table_name="agent_mailbox")
    op.drop_table("agent_mailbox")
