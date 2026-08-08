"""durable multi-agent and tool-runtime activity events (frontend Fase B)

Revision ID: 0006_multi_agent_and_tool_events
Revises: 0005_event_stream_bindings
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_multi_agent_and_tool_events"
down_revision = "0005_event_stream_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("multi_agent_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("event_id", sa.String(255), nullable=False), sa.Column("event_type", sa.String(96), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("workspace_id", sa.String(255), nullable=True), sa.Column("agent_id", sa.String(255), nullable=True), sa.Column("execution_id", sa.String(255), nullable=True), sa.Column("correlation_id", sa.String(255), nullable=False), sa.Column("causation_id", sa.String(255), nullable=True), sa.Column("sequence", sa.Integer(), nullable=True), sa.Column("classification", sa.String(32), nullable=False), sa.Column("event", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("event_id", name="uq_multi_agent_events_event_id"))
    op.create_index("ix_multi_agent_events_scope", "multi_agent_events", ["user_id", "execution_id"])
    op.create_table("tool_activity_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("event_id", sa.String(255), nullable=False), sa.Column("event_type", sa.String(96), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("workspace_id", sa.String(255), nullable=True), sa.Column("agent_id", sa.String(255), nullable=False), sa.Column("execution_id", sa.String(255), nullable=False), sa.Column("correlation_id", sa.String(255), nullable=False), sa.Column("invocation_id", sa.String(255), nullable=False), sa.Column("event", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("event_id", name="uq_tool_activity_events_event_id"))
    op.create_index("ix_tool_activity_events_scope", "tool_activity_events", ["user_id", "execution_id"])


def downgrade() -> None:
    op.drop_table("tool_activity_events")
    op.drop_table("multi_agent_events")
