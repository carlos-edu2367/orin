"""Tests for Alembic migration 0063_agent_message_delivery (WS-B0a)."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import (
    conversation_agent_controls,
    conversation_input_queue,
)

PREVIOUS_REVISION = "0062_subscription_quota_snapshots"
TARGET_REVISION = "0063_agent_message_delivery"
NEW_CONTROL_COLS = {"sender_kind", "sender_agent_id", "delivered_at"}
NEW_QUEUE_COLS = {"origin", "origin_conversation_id"}


def _seed_pre_0063_rows(engine) -> None:
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO conversation_agent_controls "
                "(control_id, conversation_id, user_id, agent_id, kind, content, created_at, consumed_at, outcome) "
                "VALUES "
                "(:c1, 'conv-1', 'u1', 'agent:conv-1:a1', 'message', 'na fila', :now, NULL, NULL), "
                "(:c2, 'conv-1', 'u1', 'agent:conv-1:a1', 'message', 'ja entregue', :now, :now, 'delivered')"
            ),
            {"c1": "ctrl_queued", "c2": "ctrl_delivered", "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO conversation_input_queue "
                "(queue_id, conversation_id, user_id, content, state, turn_id, idempotency_key, created_at, updated_at) "
                "VALUES "
                "(:q1, 'conv-1', 'u1', 'ola do usuario', 'pending', 'turn-1', 'idem-1', :now, :now)"
            ),
            {"q1": "q_existing", "now": now},
        )


def test_migration_0063_upgrade_preserves_rows_is_idempotent_and_downgrades_cleanly(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'mig 0063 ação.db').as_posix()}")
    try:
        upgrade(engine, PREVIOUS_REVISION)
        inspector = inspect(engine)
        ctrl_cols_before = {col["name"] for col in inspector.get_columns("conversation_agent_controls")}
        queue_cols_before = {col["name"] for col in inspector.get_columns("conversation_input_queue")}
        assert NEW_CONTROL_COLS.isdisjoint(ctrl_cols_before)
        assert NEW_QUEUE_COLS.isdisjoint(queue_cols_before)

        _seed_pre_0063_rows(engine)

        # Upgrade to head and verify idempotence on a second call.
        upgrade(engine, "head")
        upgrade(engine, "head")

        with engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == TARGET_REVISION

        inspector = inspect(engine)
        ctrl_cols_after = {col["name"] for col in inspector.get_columns("conversation_agent_controls")}
        queue_cols_after = {col["name"] for col in inspector.get_columns("conversation_input_queue")}
        assert ctrl_cols_after == {col.name for col in conversation_agent_controls.columns}
        assert queue_cols_after == {col.name for col in conversation_input_queue.columns}
        assert NEW_CONTROL_COLS <= ctrl_cols_after
        assert NEW_QUEUE_COLS <= queue_cols_after

        # Verify backfilled defaults and preserved values on existing rows.
        with engine.connect() as conn:
            ctrl_rows = {
                row["control_id"]: dict(row)
                for row in conn.execute(
                    text(
                        "SELECT control_id, outcome, sender_kind, sender_agent_id, delivered_at "
                        "FROM conversation_agent_controls"
                    )
                ).mappings()
            }
            assert ctrl_rows["ctrl_queued"] == {
                "control_id": "ctrl_queued",
                "outcome": None,
                "sender_kind": "user",
                "sender_agent_id": None,
                "delivered_at": None,
            }
            assert ctrl_rows["ctrl_delivered"] == {
                "control_id": "ctrl_delivered",
                "outcome": "delivered",
                "sender_kind": "user",
                "sender_agent_id": None,
                "delivered_at": None,
            }

            queue_row = dict(
                conn.execute(
                    text(
                        "SELECT queue_id, content, state, origin, origin_conversation_id "
                        "FROM conversation_input_queue WHERE queue_id = 'q_existing'"
                    )
                ).mappings().one()
            )
            assert queue_row == {
                "queue_id": "q_existing",
                "content": "ola do usuario",
                "state": "pending",
                "origin": "user",
                "origin_conversation_id": None,
            }

        # Direct migration upgrade() invocation when columns already exist must remain a no-op.
        mig = importlib.import_module(
            "agentos.persistence.postgres.migrations.versions.0063_agent_message_delivery"
        )
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.upgrade()

        # Downgrade to 0062 removes the 5 columns cleanly, and running downgrade twice does not fail.
        downgrade(engine, PREVIOUS_REVISION)
        downgrade(engine, PREVIOUS_REVISION)

        with engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == PREVIOUS_REVISION

        inspector = inspect(engine)
        ctrl_cols_down = {col["name"] for col in inspector.get_columns("conversation_agent_controls")}
        queue_cols_down = {col["name"] for col in inspector.get_columns("conversation_input_queue")}
        assert NEW_CONTROL_COLS.isdisjoint(ctrl_cols_down)
        assert NEW_QUEUE_COLS.isdisjoint(queue_cols_down)

        # Existing rows survived the SQLite batch_alter_table downgrade.
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_agent_controls")).scalar() == 2
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_input_queue")).scalar() == 1

        # Direct migration downgrade() invocation when columns are already absent must remain a no-op.
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.downgrade()

        # Re-upgrade cleanly after downgrade.
        upgrade(engine, "head")
        inspector = inspect(engine)
        assert NEW_CONTROL_COLS <= {col["name"] for col in inspector.get_columns("conversation_agent_controls")}
        assert NEW_QUEUE_COLS <= {col["name"] for col in inspector.get_columns("conversation_input_queue")}
    finally:
        engine.dispose()


def test_store_patches_message_columns_and_migration_0063_stays_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'store_patch_0063.db').as_posix()}")
    try:
        upgrade(engine, PREVIOUS_REVISION)
        _seed_pre_0063_rows(engine)

        PostgresChatStore(engine)
        inspector = inspect(engine)
        assert NEW_CONTROL_COLS <= {col["name"] for col in inspector.get_columns("conversation_agent_controls")}
        assert NEW_QUEUE_COLS <= {col["name"] for col in inspector.get_columns("conversation_input_queue")}

        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT sender_kind FROM conversation_agent_controls WHERE control_id = 'ctrl_queued'")
            ).scalar() == "user"
            assert conn.execute(
                text("SELECT origin FROM conversation_input_queue WHERE queue_id = 'q_existing'")
            ).scalar() == "user"

        upgrade(engine, "head")
        with engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == TARGET_REVISION
    finally:
        engine.dispose()
