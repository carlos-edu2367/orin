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


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # The store creates these tables and columns itself when it starts on a database that skipped migrations,
    # so each one is created or added only when it is missing: 0048 succeeds whichever of the two runs first.
    bind = op.get_bind()
    if "conversation_input_queue" not in _tables(bind):
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
    if "ix_conversation_input_queue_pending" not in _indexes(bind, "conversation_input_queue"):
        op.create_index("ix_conversation_input_queue_pending", "conversation_input_queue", ["conversation_id", "state", "id"])
    if "idempotency_key" not in _columns(bind, "conversation_input_queue"):
        with op.batch_alter_table("conversation_input_queue") as batch:
            batch.add_column(sa.Column("idempotency_key", sa.String(255), nullable=True))
    if "ux_conversation_input_queue_idempotency" not in _indexes(bind, "conversation_input_queue"):
        op.create_index("ux_conversation_input_queue_idempotency", "conversation_input_queue", ["conversation_id", "idempotency_key"], unique=True)
    if "conversation_prompt_snapshots" not in _tables(bind):
        op.create_table(
            "conversation_prompt_snapshots",
            sa.Column("conversation_id", sa.String(255), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False),
            sa.Column("snapshot_key", sa.String(64), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "conversation_compact_boundaries" not in _tables(bind):
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
    if "ix_conversation_compact_boundaries_conversation" not in _indexes(bind, "conversation_compact_boundaries"):
        op.create_index("ix_conversation_compact_boundaries_conversation", "conversation_compact_boundaries", ["conversation_id", "id"])
    if "conversation_sessions" not in _tables(bind):
        op.create_table(
            "conversation_sessions",
            sa.Column("conversation_id", sa.String(255), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_source", sa.String(16), nullable=False),
            sa.Column("start_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "turn_kind" not in _columns(bind, "conversation_turns"):
        with op.batch_alter_table("conversation_turns") as batch:
            batch.add_column(sa.Column("turn_kind", sa.String(16), nullable=True))
    if "lease_renewed_at" not in _columns(bind, "conversation_dispatches"):
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
