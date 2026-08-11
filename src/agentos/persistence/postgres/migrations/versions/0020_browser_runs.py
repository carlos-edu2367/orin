"""Persist bounded browser run projections and artifact references."""
from alembic import op
import sqlalchemy as sa

revision = "0020_browser_runs"
down_revision = "0019_terminal_runs"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "browser_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("session_ref", sa.String(255), nullable=False),
        sa.Column("page_ref", sa.String(255)),
        sa.Column("url_host", sa.String(255)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("artifact_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_browser_runs_owner_execution", "browser_runs", ["user_id", "execution_id"])

def downgrade() -> None:
    op.drop_index("ix_browser_runs_owner_execution", table_name="browser_runs")
    op.drop_table("browser_runs")
