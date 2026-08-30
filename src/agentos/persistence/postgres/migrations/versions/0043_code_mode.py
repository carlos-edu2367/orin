"""durable Code mode projections

Revision ID: 0043_code_mode
Revises: 0042_conversation_turn_steps
"""
from alembic import op
import sqlalchemy as sa


revision = "0043_code_mode"
down_revision = "0042_conversation_turn_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_turns", sa.Column("code_mode", sa.String(16), nullable=True))
    op.create_table(
        "code_mode_runs",
        sa.Column("run_id", sa.String(255), primary_key=True),
        sa.Column("execution_id", sa.String(255), nullable=False, unique=True),
        sa.Column("turn_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("work_kind", sa.String(32), nullable=False), sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("autonomy", sa.String(32), nullable=False), sa.Column("plan_path", sa.String(1024), nullable=True),
        sa.Column("plan_versioned", sa.Boolean(), nullable=True), sa.Column("completion_kind", sa.String(32), nullable=True),
        sa.Column("caveats", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_mode_runs_conversation", "code_mode_runs", ["conversation_id", "created_at"])
    op.create_index("ix_code_mode_runs_execution", "code_mode_runs", ["execution_id"])
    op.create_table(
        "code_mode_checks",
        sa.Column("check_id", sa.String(255), primary_key=True), sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("category", sa.String(32), nullable=False), sa.Column("label", sa.String(512), nullable=False),
        sa.Column("state", sa.String(16), nullable=False), sa.Column("evidence_ref", sa.String(1024), nullable=True),
        sa.Column("details", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_mode_checks_run", "code_mode_checks", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_code_mode_checks_run", table_name="code_mode_checks")
    op.drop_table("code_mode_checks")
    op.drop_index("ix_code_mode_runs_execution", table_name="code_mode_runs")
    op.drop_index("ix_code_mode_runs_conversation", table_name="code_mode_runs")
    op.drop_table("code_mode_runs")
    op.drop_column("conversation_turns", "code_mode")
