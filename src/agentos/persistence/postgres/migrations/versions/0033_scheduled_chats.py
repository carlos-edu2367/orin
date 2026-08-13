"""add user scheduled-chat tasks and scheduled turn marker

Revision ID: 0033_scheduled_chats
Revises: 0032_message_attachments
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_scheduled_chats"
down_revision = "0032_message_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_chat_tasks",
        sa.Column("task_id", sa.String(255), primary_key=True),
        sa.Column("schedule_id", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("message", sa.String(16000), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column("conversation_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.schedule_id"], name="fk_scheduled_chat_schedule"),
    )
    op.create_index("ix_scheduled_chat_user", "scheduled_chat_tasks", ["user_id", "updated_at"])
    op.add_column("conversation_turns", sa.Column("scheduled_by_schedule_id", sa.String(255), nullable=True))
    op.create_index("ix_conversation_turns_schedule", "conversation_turns", ["scheduled_by_schedule_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_conversation_turns_schedule", table_name="conversation_turns")
    op.drop_column("conversation_turns", "scheduled_by_schedule_id")
    op.drop_index("ix_scheduled_chat_user", table_name="scheduled_chat_tasks")
    op.drop_table("scheduled_chat_tasks")
