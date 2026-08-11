"""persist a compact ledger of the tools an agent ran in a conversation.

Revision ID: 0029_conversation_tool_records
Revises: 0028_projects
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0029_conversation_tool_records"
down_revision = "0028_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_tool_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("record_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_tool_record_sequence"),
    )
    op.create_index("ix_conversation_tool_records_conversation", "conversation_tool_records", ["conversation_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_conversation_tool_records_conversation", table_name="conversation_tool_records")
    op.drop_table("conversation_tool_records")
