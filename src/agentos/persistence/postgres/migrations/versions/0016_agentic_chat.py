"""persist agentic chat conversations and durable turns

Revision ID: 0016_agentic_chat
Revises: 0015_agent_config_selection
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_agentic_chat"
down_revision = "0015_agent_config_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("conversations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("conversation_id", sa.String(255), nullable=False, unique=True), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("title", sa.String(160), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("model_id", sa.String(512), nullable=False), sa.Column("state", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at"])
    op.create_table("conversation_messages", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("message_id", sa.String(255), nullable=False, unique=True), sa.Column("conversation_id", sa.String(255), nullable=False), sa.Column("turn_id", sa.String(255)), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.Column("content", sa.String(16000), nullable=False), sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("false")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_message_sequence"), sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_message_role"))
    op.create_index("ix_conversation_messages_history", "conversation_messages", ["conversation_id", "sequence"])
    op.create_table("conversation_turns", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("turn_id", sa.String(255), nullable=False, unique=True), sa.Column("conversation_id", sa.String(255), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("execution_id", sa.String(255), nullable=False, unique=True), sa.Column("user_message_id", sa.String(255), nullable=False), sa.Column("assistant_message_id", sa.String(255), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("model_id", sa.String(512), nullable=False), sa.Column("state", sa.String(32), nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("user_id", "idempotency_key", name="uq_conversation_turn_idempotency"))
    op.create_index("ix_conversation_turns_conversation", "conversation_turns", ["conversation_id", "created_at"])
    op.create_table("conversation_dispatches", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("turn_id", sa.String(255), nullable=False, unique=True), sa.Column("state", sa.String(32), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_error", sa.String(96)), sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False), sa.Column("acquired_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversation_dispatch_state", "conversation_dispatches", ["state", "queued_at"])
    op.create_table("conversation_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("conversation_id", sa.String(255), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("event_type", sa.String(64), nullable=False), sa.Column("message_id", sa.String(255)), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversation_events_cursor", "conversation_events", ["conversation_id", "id"])
    op.create_table("runtime_heartbeats", sa.Column("component", sa.String(64), primary_key=True), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    for name, table in (("ix_conversation_events_cursor", "conversation_events"), ("ix_conversation_dispatch_state", "conversation_dispatches"), ("ix_conversation_turns_conversation", "conversation_turns"), ("ix_conversation_messages_history", "conversation_messages"), ("ix_conversations_user_updated", "conversations")):
        op.drop_index(name, table_name=table)
    for table in ("runtime_heartbeats", "conversation_events", "conversation_dispatches", "conversation_turns", "conversation_messages", "conversations"):
        op.drop_table(table)
