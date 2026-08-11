"""persist redacted tool invocation state for restart recovery"""
from alembic import op
import sqlalchemy as sa

revision = "0023_tool_invocations"
down_revision = "0022_provider_ciphertext"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("invocation_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(255), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("tool_id", sa.String(255), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tool_invocations_scope", "tool_invocations", ["user_id", "execution_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_scope", table_name="tool_invocations")
    op.drop_table("tool_invocations")
