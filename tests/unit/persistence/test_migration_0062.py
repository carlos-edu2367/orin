"""Tests for Alembic migration 0062_subscription_quota_snapshots."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import (
    model_context_observations,
    subscription_quota_snapshots,
)

PREVIOUS_REVISION = "0061_activity_events_overview_index"
TARGET_REVISION = "0062_subscription_quota_snapshots"
LOOKUP_INDEX = "ix_subscription_quota_snapshots_lookup"


def test_migration_0062_upgrade_downgrade_and_idempotence(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'mig 0062 ação.db').as_posix()}")
    try:
        upgrade(engine, PREVIOUS_REVISION)
        inspector = inspect(engine)
        tables_before = set(inspector.get_table_names())
        assert "subscription_quota_snapshots" not in tables_before
        assert "model_context_observations" not in tables_before
        turn_cols_before = {col["name"] for col in inspector.get_columns("conversation_turns")}
        assert "effort" not in turn_cols_before

        upgrade(engine, "head")
        # Running head upgrade a second time must be a harmless no-op.
        upgrade(engine, "head")

        with engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == TARGET_REVISION

        inspector = inspect(engine)
        tables_after = set(inspector.get_table_names())
        assert "subscription_quota_snapshots" in tables_after
        assert "model_context_observations" in tables_after

        quota_cols = {col["name"] for col in inspector.get_columns("subscription_quota_snapshots")}
        assert quota_cols == {col.name for col in subscription_quota_snapshots.columns}
        assert quota_cols == {
            "id",
            "user_id",
            "provider",
            "window_kind",
            "used_percent",
            "status",
            "resets_at",
            "source",
            "scope_model",
            "observed_at",
            "raw",
        }

        obs_cols = {col["name"] for col in inspector.get_columns("model_context_observations")}
        assert obs_cols == {col.name for col in model_context_observations.columns}
        assert obs_cols == {
            "user_id",
            "provider",
            "model_id",
            "observed_limit_tokens",
            "source",
            "first_message_tokens",
            "cli_version",
            "account_credits_on",
            "observed_at",
            "invalidated_at",
        }
        pk = inspector.get_pk_constraint("model_context_observations")
        assert pk["constrained_columns"] == ["user_id", "provider", "model_id"]

        indexes = {ix["name"] for ix in inspector.get_indexes("subscription_quota_snapshots")}
        assert LOOKUP_INDEX in indexes

        turn_cols_after = {col["name"] for col in inspector.get_columns("conversation_turns")}
        assert "effort" in turn_cols_after

        downgrade(engine, PREVIOUS_REVISION)
        inspector = inspect(engine)
        tables_down = set(inspector.get_table_names())
        assert "subscription_quota_snapshots" not in tables_down
        assert "model_context_observations" not in tables_down
        turn_cols_down = {col["name"] for col in inspector.get_columns("conversation_turns")}
        assert "effort" not in turn_cols_down

        # Re-upgrade cleanly after downgrade.
        upgrade(engine, "head")
        inspector = inspect(engine)
        assert "subscription_quota_snapshots" in set(inspector.get_table_names())
        assert "model_context_observations" in set(inspector.get_table_names())
        assert "effort" in {col["name"] for col in inspector.get_columns("conversation_turns")}
        assert LOOKUP_INDEX in {ix["name"] for ix in inspector.get_indexes("subscription_quota_snapshots")}
    finally:
        engine.dispose()


def test_store_patches_effort_column_and_migration_0062_stays_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'store_patch_0062.db').as_posix()}")
    try:
        upgrade(engine, PREVIOUS_REVISION)
        PostgresChatStore(engine)
        assert "effort" in {col["name"] for col in inspect(engine).get_columns("conversation_turns")}

        upgrade(engine, "head")
        with engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == TARGET_REVISION
        assert "subscription_quota_snapshots" in set(inspect(engine).get_table_names())
        assert "model_context_observations" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
