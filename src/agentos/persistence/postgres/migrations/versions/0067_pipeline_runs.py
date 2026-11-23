"""Add pipeline_runs and pipeline_step_runs, the durable journal of pipeline executions (WS-C3, Spec 7.6)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_pipeline_runs"
down_revision = "0066_agent_runs_and_task_board"
branch_labels = None
depends_on = None

RUNS_TABLE = "pipeline_runs"
STEPS_TABLE = "pipeline_step_runs"

IX_RUNS_CONVERSATION = "ix_pipeline_runs_conversation_started"
IX_RUNS_USER_STATE = "ix_pipeline_runs_user_state"
IX_STEPS_REUSE = "ix_pipeline_step_runs_reuse"
IX_STEPS_AGENT = "ix_pipeline_step_runs_agent"


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _index_names(bind, table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table) if ix.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if RUNS_TABLE not in tables:
        op.create_table(
            RUNS_TABLE,
            sa.Column("run_id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("conversation_id", sa.String(length=255), nullable=False),
            sa.Column("workspace_root", sa.String(length=1024), nullable=False),
            sa.Column("pipeline_id", sa.String(length=64), nullable=False),
            sa.Column("definition_hash", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("inputs", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("used_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("used_cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("used_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("definition_snapshot", sa.Text(), nullable=False, server_default=""),
            sa.Column("turn_id", sa.String(length=255), nullable=True),
            sa.Column("stop_reason", sa.String(length=32), nullable=True),
        )
        tables.add(RUNS_TABLE)

    if RUNS_TABLE in tables:
        run_indexes = _index_names(bind, RUNS_TABLE)
        if IX_RUNS_CONVERSATION not in run_indexes:
            op.create_index(IX_RUNS_CONVERSATION, RUNS_TABLE, ["conversation_id", sa.text("started_at DESC")])
        if IX_RUNS_USER_STATE not in run_indexes:
            op.create_index(IX_RUNS_USER_STATE, RUNS_TABLE, ["user_id", "state"])

    if STEPS_TABLE not in tables:
        op.create_table(
            STEPS_TABLE,
            sa.Column("run_id", sa.String(length=64), primary_key=True),
            sa.Column("step_id", sa.String(length=64), primary_key=True),
            sa.Column("item_key", sa.String(length=255), primary_key=True, server_default=""),
            sa.Column("attempt", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("input_hash", sa.String(length=64), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("agent_id", sa.String(length=64), nullable=True),
            sa.Column("output", sa.JSON(), nullable=True),
            sa.Column("error", sa.String(length=2000), nullable=True),
            sa.Column("loop_count", sa.SmallInteger(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        tables.add(STEPS_TABLE)

    if STEPS_TABLE in tables:
        step_indexes = _index_names(bind, STEPS_TABLE)
        if IX_STEPS_REUSE not in step_indexes:
            op.create_index(IX_STEPS_REUSE, STEPS_TABLE, ["run_id", "step_id", "loop_count"])
        if IX_STEPS_AGENT not in step_indexes:
            op.create_index(IX_STEPS_AGENT, STEPS_TABLE, ["agent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if STEPS_TABLE in tables:
        step_indexes = _index_names(bind, STEPS_TABLE)
        for name in (IX_STEPS_AGENT, IX_STEPS_REUSE):
            if name in step_indexes:
                op.drop_index(name, table_name=STEPS_TABLE)
        op.drop_table(STEPS_TABLE)

    if RUNS_TABLE in tables:
        run_indexes = _index_names(bind, RUNS_TABLE)
        for name in (IX_RUNS_USER_STATE, IX_RUNS_CONVERSATION):
            if name in run_indexes:
                op.drop_index(name, table_name=RUNS_TABLE)
        op.drop_table(RUNS_TABLE)
