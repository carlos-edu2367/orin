"""Add subscription_quota_snapshots, model_context_observations and conversation_turns.effort."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062_subscription_quota_snapshots"
down_revision = "0061_activity_events_overview_index"
branch_labels = None
depends_on = None

QUOTA_TABLE = "subscription_quota_snapshots"
QUOTA_INDEX = "ix_subscription_quota_snapshots_lookup"
OBS_TABLE = "model_context_observations"
TURNS_TABLE = "conversation_turns"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if QUOTA_TABLE not in tables:
        op.create_table(
            QUOTA_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("window_kind", sa.String(length=32), nullable=False),
            sa.Column("used_percent", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("resets_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("scope_model", sa.String(length=64), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("raw", sa.JSON(), nullable=True),
        )
        tables.add(QUOTA_TABLE)

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(QUOTA_TABLE)} if QUOTA_TABLE in tables else set()
    if QUOTA_INDEX not in existing_indexes:
        op.create_index(
            QUOTA_INDEX,
            QUOTA_TABLE,
            ["user_id", "provider", "window_kind", sa.text("observed_at DESC")],
        )

    if OBS_TABLE not in tables:
        op.create_table(
            OBS_TABLE,
            sa.Column("user_id", sa.String(length=255), primary_key=True),
            sa.Column("provider", sa.String(length=64), primary_key=True),
            sa.Column("model_id", sa.String(length=128), primary_key=True),
            sa.Column("observed_limit_tokens", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("first_message_tokens", sa.Integer(), nullable=True),
            sa.Column("cli_version", sa.String(length=32), nullable=True),
            sa.Column("account_credits_on", sa.Boolean(), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if TURNS_TABLE in tables:
        turn_columns = {col["name"] for col in inspector.get_columns(TURNS_TABLE)}
        if "effort" not in turn_columns:
            op.add_column(TURNS_TABLE, sa.Column("effort", sa.String(length=16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TURNS_TABLE in tables:
        turn_columns = {col["name"] for col in inspector.get_columns(TURNS_TABLE)}
        if "effort" in turn_columns:
            with op.batch_alter_table(TURNS_TABLE) as batch_op:
                batch_op.drop_column("effort")

    if OBS_TABLE in tables:
        op.drop_table(OBS_TABLE)

    if QUOTA_TABLE in tables:
        existing_indexes = {ix["name"] for ix in inspector.get_indexes(QUOTA_TABLE)}
        if QUOTA_INDEX in existing_indexes:
            op.drop_index(QUOTA_INDEX, table_name=QUOTA_TABLE)
        op.drop_table(QUOTA_TABLE)
