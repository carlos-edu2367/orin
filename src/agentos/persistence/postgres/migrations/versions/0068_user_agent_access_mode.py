"""Add optional access_mode column to user_agents (Frente B: prompt_only | read_only | full)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0068_user_agent_access_mode"
down_revision = "0067_pipeline_runs"
branch_labels = None
depends_on = None

TABLE_NAME = "user_agents"
COLUMN_NAME = "access_mode"


def _column_names(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _column_names(bind, TABLE_NAME)
    if cols and COLUMN_NAME not in cols:
        op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = _column_names(bind, TABLE_NAME)
    if COLUMN_NAME in cols:
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_column(COLUMN_NAME)
