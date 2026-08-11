"""Persist bounded terminal run projections."""
from alembic import op
import sqlalchemy as sa

revision = "0019_terminal_runs"
down_revision = "0018_agentic_artifacts"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "terminal_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("command_ref", sa.String(255), nullable=False),
        sa.Column("output_artifact_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_terminal_runs_owner_execution", "terminal_runs", ["user_id", "execution_id"])

def downgrade() -> None:
    op.drop_index("ix_terminal_runs_owner_execution", table_name="terminal_runs")
    op.drop_table("terminal_runs")
