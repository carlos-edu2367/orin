"""Add composite index for conversation overview activity queries."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_activity_events_overview_index"
down_revision = "0060_discord_bridge_and_session_watches"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_conversation_activity_conv_type_id"
TABLE_NAME = "conversation_activity_events"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    if INDEX_NAME not in existing:
        op.create_index(INDEX_NAME, TABLE_NAME, ["conversation_id", "event_type", "id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    if INDEX_NAME in existing:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
