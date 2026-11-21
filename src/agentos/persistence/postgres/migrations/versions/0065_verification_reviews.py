"""Add verification_reviews and verification_findings tables (WS-A6, Spec 5.6)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065_verification_reviews"
down_revision = "0064_memory_lifecycle_and_agent_scope"
branch_labels = None
depends_on = None

REVIEWS_TABLE = "verification_reviews"
FINDINGS_TABLE = "verification_findings"
IX_USER_STARTED = "ix_verification_reviews_user_started"
IX_CONVERSATION_TURN = "ix_verification_reviews_conversation_turn"


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _index_names(bind, table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table) if ix.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if REVIEWS_TABLE not in tables:
        op.create_table(
            REVIEWS_TABLE,
            sa.Column("review_id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("conversation_id", sa.String(length=255), nullable=False),
            sa.Column("turn_id", sa.String(length=255), nullable=False),
            sa.Column("agent_id", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("round", sa.SmallInteger(), nullable=False),
            sa.Column("trigger_reason", sa.String(length=32), nullable=False),
            sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lines_changed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("author_provider", sa.String(length=64), nullable=False),
            sa.Column("author_model", sa.String(length=128), nullable=False),
            sa.Column("author_effort", sa.String(length=16), nullable=True),
            sa.Column("reviewer_provider", sa.String(length=64), nullable=True),
            sa.Column("reviewer_model", sa.String(length=128), nullable=True),
            sa.Column("reviewer_effort", sa.String(length=16), nullable=True),
            sa.Column("policy", sa.String(length=16), nullable=False),
            sa.Column("fallback_step", sa.SmallInteger(), nullable=False),
            sa.Column("verdict", sa.String(length=16), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        tables.add(REVIEWS_TABLE)

    existing_indexes = _index_names(bind, REVIEWS_TABLE) if REVIEWS_TABLE in tables else set()
    if IX_USER_STARTED not in existing_indexes:
        op.create_index(IX_USER_STARTED, REVIEWS_TABLE, ["user_id", "started_at"])
    if IX_CONVERSATION_TURN not in existing_indexes:
        op.create_index(IX_CONVERSATION_TURN, REVIEWS_TABLE, ["conversation_id", "turn_id"])

    if FINDINGS_TABLE not in tables:
        op.create_table(
            FINDINGS_TABLE,
            sa.Column("review_id", sa.String(length=64), primary_key=True),
            sa.Column("finding_id", sa.String(length=64), primary_key=True),
            sa.Column("severity", sa.String(length=16), nullable=False),
            sa.Column("category", sa.String(length=16), nullable=False),
            sa.Column("has_evidence", sa.Boolean(), nullable=False),
            sa.Column("resolution", sa.String(length=16), nullable=True),
            sa.Column("resolution_reason", sa.String(length=1000), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if FINDINGS_TABLE in tables:
        op.drop_table(FINDINGS_TABLE)

    if REVIEWS_TABLE in tables:
        existing_indexes = _index_names(bind, REVIEWS_TABLE)
        if IX_CONVERSATION_TURN in existing_indexes:
            op.drop_index(IX_CONVERSATION_TURN, table_name=REVIEWS_TABLE)
        if IX_USER_STARTED in existing_indexes:
            op.drop_index(IX_USER_STARTED, table_name=REVIEWS_TABLE)
        op.drop_table(REVIEWS_TABLE)
