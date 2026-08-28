"""keep the agentic trajectory of a turn so the next turn can read it

Revision ID: 0042_conversation_turn_steps
Revises: 0041_turn_quality_metrics
"""
from alembic import op
import sqlalchemy as sa


revision = "0042_conversation_turn_steps"
down_revision = "0041_turn_quality_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_turn_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("step_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("tool_call_id", sa.String(255), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("content_bytes", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", "agent_id", "sequence", name="uq_conversation_turn_step_sequence"),
    )
    op.create_index(
        "ix_conversation_turn_steps_turn",
        "conversation_turn_steps",
        ["conversation_id", "turn_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_turn_steps_turn", table_name="conversation_turn_steps")
    op.drop_table("conversation_turn_steps")
