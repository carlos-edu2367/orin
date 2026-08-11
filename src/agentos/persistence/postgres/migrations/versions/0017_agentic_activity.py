"""persist bounded owner-scoped agentic conversation activity

Revision ID: 0017_agentic_activity
Revises: 0016_agentic_chat
"""
from alembic import op
import sqlalchemy as sa


revision = "0017_agentic_activity"
down_revision = "0016_agentic_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_activity_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("parent_agent_id", sa.String(255)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_conversation_activity_event_id"),
        sa.UniqueConstraint(
            "user_id", "conversation_id", "turn_id", "sequence",
            name="uq_conversation_activity_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_conversation_activity_sequence_positive"),
    )
    op.create_index(
        "ix_conversation_activity_conversation_cursor",
        "conversation_activity_events",
        ["conversation_id", "id"],
    )
    op.create_index(
        "ix_conversation_activity_user_created",
        "conversation_activity_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_activity_turn_sequence",
        "conversation_activity_events",
        ["turn_id", "sequence"],
    )


def downgrade() -> None:
    for name in (
        "ix_conversation_activity_turn_sequence",
        "ix_conversation_activity_user_created",
        "ix_conversation_activity_conversation_cursor",
    ):
        op.drop_index(name, table_name="conversation_activity_events")
    op.drop_table("conversation_activity_events")
