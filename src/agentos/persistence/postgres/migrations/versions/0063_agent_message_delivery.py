"""Add message delivery columns to conversation_agent_controls and conversation_input_queue."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0063_agent_message_delivery"
down_revision = "0062_subscription_quota_snapshots"
branch_labels = None
depends_on = None

CONTROLS_TABLE = "conversation_agent_controls"
QUEUE_TABLE = "conversation_input_queue"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if CONTROLS_TABLE in tables:
        control_columns = {col["name"] for col in inspector.get_columns(CONTROLS_TABLE)}
        if "sender_kind" not in control_columns:
            op.add_column(
                CONTROLS_TABLE,
                sa.Column("sender_kind", sa.String(length=16), nullable=False, server_default="user"),
            )
        if "sender_agent_id" not in control_columns:
            op.add_column(
                CONTROLS_TABLE,
                sa.Column("sender_agent_id", sa.String(length=64), nullable=True),
            )
        if "delivered_at" not in control_columns:
            op.add_column(
                CONTROLS_TABLE,
                sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            )

    if QUEUE_TABLE in tables:
        queue_columns = {col["name"] for col in inspector.get_columns(QUEUE_TABLE)}
        if "origin" not in queue_columns:
            op.add_column(
                QUEUE_TABLE,
                sa.Column("origin", sa.String(length=16), nullable=False, server_default="user"),
            )
        if "origin_conversation_id" not in queue_columns:
            op.add_column(
                QUEUE_TABLE,
                sa.Column("origin_conversation_id", sa.String(length=255), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if QUEUE_TABLE in tables:
        queue_columns = {col["name"] for col in inspector.get_columns(QUEUE_TABLE)}
        to_drop = [col for col in ("origin_conversation_id", "origin") if col in queue_columns]
        if to_drop:
            with op.batch_alter_table(QUEUE_TABLE) as batch_op:
                for col in to_drop:
                    batch_op.drop_column(col)

    if CONTROLS_TABLE in tables:
        control_columns = {col["name"] for col in inspector.get_columns(CONTROLS_TABLE)}
        to_drop = [col for col in ("delivered_at", "sender_agent_id", "sender_kind") if col in control_columns]
        if to_drop:
            with op.batch_alter_table(CONTROLS_TABLE) as batch_op:
                for col in to_drop:
                    batch_op.drop_column(col)
