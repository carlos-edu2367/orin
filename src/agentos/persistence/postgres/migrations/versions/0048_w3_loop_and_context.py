"""W3: input queue, prompt snapshots, compaction boundaries, session state, turn kind and dispatch leases

Revision ID: 0048_w3_loop_and_context
Revises: 0047_conversation_mode_and_permission
"""
from alembic import op
import sqlalchemy as sa


revision = "0048_w3_loop_and_context"
down_revision = "0047_conversation_mode_and_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_input_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queue_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("content", sa.String(16000), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_input_queue_pending", "conversation_input_queue", ["conversation_id", "state", "id"])
    op.create_table(
        "conversation_prompt_snapshots",
        sa.Column("conversation_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("snapshot_key", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "conversation_compact_boundaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("boundary_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("compact_trigger", sa.String(16), nullable=False),
        sa.Column("pre_tokens", sa.Integer(), nullable=False),
        sa.Column("post_tokens", sa.Integer(), nullable=True),
        sa.Column("cumulative_dropped_tokens", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("through_sequence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_compact_boundaries_conversation", "conversation_compact_boundaries", ["conversation_id", "id"])
    op.create_table(
        "conversation_sessions",
        sa.Column("conversation_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_source", sa.String(16), nullable=False),
        sa.Column("start_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("conversation_turns") as batch:
        batch.add_column(sa.Column("turn_kind", sa.String(16), nullable=True))
    with op.batch_alter_table("conversation_dispatches") as batch:
        batch.add_column(sa.Column("lease_renewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversation_dispatches") as batch:
        batch.drop_column("lease_renewed_at")
    with op.batch_alter_table("conversation_turns") as batch:
        batch.drop_column("turn_kind")
    op.drop_table("conversation_sessions")
    op.drop_index("ix_conversation_compact_boundaries_conversation", table_name="conversation_compact_boundaries")
    op.drop_table("conversation_compact_boundaries")
    op.drop_table("conversation_prompt_snapshots")
    op.drop_index("ix_conversation_input_queue_pending", table_name="conversation_input_queue")
    op.drop_table("conversation_input_queue")
