"""Add agent_runs, agent_worktrees, task_board_items and user_agents.isolation (WS-B0, Spec 6.3)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0066_agent_runs_and_task_board"
down_revision = "0065_verification_reviews"
branch_labels = None
depends_on = None

USER_AGENTS_TABLE = "user_agents"
AGENT_RUNS_TABLE = "agent_runs"
AGENT_WORKTREES_TABLE = "agent_worktrees"
TASK_BOARD_TABLE = "task_board_items"

IX_AGENT_RUNS_CONV_STATE = "ix_agent_runs_conversation_state"
IX_AGENT_RUNS_STATE = "ix_agent_runs_state"
IX_AGENT_WORKTREES_WORKSPACE = "ix_agent_worktrees_workspace_root"
IX_TASK_BOARD_CONV_STATUS = "ix_task_board_items_conversation_status"


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _column_names(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table) if ix.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    # 1. user_agents.isolation VARCHAR(16) NULL
    if USER_AGENTS_TABLE in tables:
        ua_cols = _column_names(bind, USER_AGENTS_TABLE)
        if "isolation" not in ua_cols:
            op.add_column(
                USER_AGENTS_TABLE,
                sa.Column("isolation", sa.String(length=16), nullable=True),
            )

    # 2. agent_runs
    if AGENT_RUNS_TABLE not in tables:
        op.create_table(
            AGENT_RUNS_TABLE,
            sa.Column("agent_id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("conversation_id", sa.String(length=255), nullable=False),
            sa.Column("turn_id", sa.String(length=255), nullable=False),
            sa.Column("parent_agent_id", sa.String(length=255), nullable=True),
            sa.Column("subagent_type", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("model_id", sa.String(length=128), nullable=True),
            sa.Column("effort", sa.String(length=16), nullable=True),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("task_digest", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("history_path", sa.String(length=512), nullable=True),
            sa.Column("worktree_path", sa.String(length=512), nullable=True),
            sa.Column("pipeline_run_id", sa.String(length=64), nullable=True),
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("result_ref", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        tables.add(AGENT_RUNS_TABLE)

    run_indexes = _index_names(bind, AGENT_RUNS_TABLE) if AGENT_RUNS_TABLE in tables else set()
    if IX_AGENT_RUNS_CONV_STATE not in run_indexes:
        op.create_index(IX_AGENT_RUNS_CONV_STATE, AGENT_RUNS_TABLE, ["conversation_id", "state"])
    if IX_AGENT_RUNS_STATE not in run_indexes:
        op.create_index(IX_AGENT_RUNS_STATE, AGENT_RUNS_TABLE, ["state"])

    # 3. agent_worktrees
    if AGENT_WORKTREES_TABLE not in tables:
        op.create_table(
            AGENT_WORKTREES_TABLE,
            sa.Column("agent_id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_root", sa.String(length=1024), nullable=False),
            sa.Column("base_commit", sa.String(length=64), nullable=False),
            sa.Column("branch", sa.String(length=255), nullable=False),
            sa.Column("merge_state", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("merge_detail", sa.JSON(), nullable=True),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        )
        tables.add(AGENT_WORKTREES_TABLE)

    wt_indexes = _index_names(bind, AGENT_WORKTREES_TABLE) if AGENT_WORKTREES_TABLE in tables else set()
    if IX_AGENT_WORKTREES_WORKSPACE not in wt_indexes:
        op.create_index(IX_AGENT_WORKTREES_WORKSPACE, AGENT_WORKTREES_TABLE, ["workspace_root"])

    # 4. task_board_items
    if TASK_BOARD_TABLE not in tables:
        op.create_table(
            TASK_BOARD_TABLE,
            sa.Column("item_id", sa.String(length=64), primary_key=True),
            sa.Column("conversation_id", sa.String(length=255), nullable=False),
            sa.Column("team_name", sa.String(length=64), nullable=True),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("owner_agent_id", sa.String(length=64), nullable=True),
            sa.Column("blocked_by", sa.JSON(), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_ref", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables.add(TASK_BOARD_TABLE)

    tb_indexes = _index_names(bind, TASK_BOARD_TABLE) if TASK_BOARD_TABLE in tables else set()
    if IX_TASK_BOARD_CONV_STATUS not in tb_indexes:
        op.create_index(IX_TASK_BOARD_CONV_STATUS, TASK_BOARD_TABLE, ["conversation_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if TASK_BOARD_TABLE in tables:
        tb_indexes = _index_names(bind, TASK_BOARD_TABLE)
        if IX_TASK_BOARD_CONV_STATUS in tb_indexes:
            op.drop_index(IX_TASK_BOARD_CONV_STATUS, table_name=TASK_BOARD_TABLE)
        op.drop_table(TASK_BOARD_TABLE)

    if AGENT_WORKTREES_TABLE in tables:
        wt_indexes = _index_names(bind, AGENT_WORKTREES_TABLE)
        if IX_AGENT_WORKTREES_WORKSPACE in wt_indexes:
            op.drop_index(IX_AGENT_WORKTREES_WORKSPACE, table_name=AGENT_WORKTREES_TABLE)
        op.drop_table(AGENT_WORKTREES_TABLE)

    if AGENT_RUNS_TABLE in tables:
        run_indexes = _index_names(bind, AGENT_RUNS_TABLE)
        if IX_AGENT_RUNS_STATE in run_indexes:
            op.drop_index(IX_AGENT_RUNS_STATE, table_name=AGENT_RUNS_TABLE)
        if IX_AGENT_RUNS_CONV_STATE in run_indexes:
            op.drop_index(IX_AGENT_RUNS_CONV_STATE, table_name=AGENT_RUNS_TABLE)
        op.drop_table(AGENT_RUNS_TABLE)

    if USER_AGENTS_TABLE in tables:
        ua_cols = _column_names(bind, USER_AGENTS_TABLE)
        if "isolation" in ua_cols:
            with op.batch_alter_table(USER_AGENTS_TABLE) as batch_op:
                batch_op.drop_column("isolation")
