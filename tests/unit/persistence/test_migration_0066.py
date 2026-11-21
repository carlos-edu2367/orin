"""Tests for Alembic migration 0066_agent_runs_and_task_board (WS-B0, Spec 6.3)."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import (
    agent_runs,
    agent_worktrees,
    task_board_items,
    user_agents,
)

PREVIOUS_REVISION = "0065_verification_reviews"
TARGET_REVISION = "0066_agent_runs_and_task_board"


def _seed_pre_0066_rows(engine) -> None:
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_agents "
                "(user_id, name, description, prompt, models_json, disallowed_tools_json, "
                "memory, enabled, created_at, updated_at) "
                "VALUES (:uid, :name, :desc, :prompt, :models, :disallowed, :memory, :enabled, :ca, :ua)"
            ),
            {
                "uid": "u-1",
                "name": "researcher",
                "desc": "Pesquisa profunda",
                "prompt": "Pesquise com rigor.",
                "models": "[]",
                "disallowed": "[]",
                "memory": "project",
                "enabled": True,
                "ca": now,
                "ua": now,
            },
        )
        conn.execute(
            text(
                "INSERT INTO conversation_agent_controls "
                "(control_id, conversation_id, user_id, agent_id, kind, content, created_at, "
                "sender_kind, sender_agent_id, delivered_at) "
                "VALUES (:cid, :conv, :uid, :aid, :kind, :content, :ca, :sk, :said, :da)"
            ),
            {
                "cid": "ctrl-0063",
                "conv": "conv-1",
                "uid": "u-1",
                "aid": "ag-1",
                "kind": "message",
                "content": "Foque nos testes.",
                "ca": now,
                "sk": "parent",
                "said": "parent-0",
                "da": now,
            },
        )
        conn.execute(
            text(
                "INSERT INTO conversation_input_queue "
                "(queue_id, conversation_id, user_id, content, state, origin, origin_conversation_id, "
                "created_at, updated_at) "
                "VALUES (:qid, :conv, :uid, :content, :state, :origin, :orig_conv, :ca, :ua)"
            ),
            {
                "qid": "q-0063",
                "conv": "conv-1",
                "uid": "u-1",
                "content": "Continuar depois",
                "state": "pending",
                "origin": "friend",
                "orig_conv": "friend-conv-9",
                "ca": now,
                "ua": now,
            },
        )


def test_migration_0066_upgrades_creates_tables_isolation_column_and_preserves_0063_data() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, PREVIOUS_REVISION)
    _seed_pre_0066_rows(engine)

    pre_inspector = inspect(engine)
    pre_tables = set(pre_inspector.get_table_names())
    assert "agent_runs" not in pre_tables
    assert "agent_worktrees" not in pre_tables
    assert "task_board_items" not in pre_tables
    assert "isolation" not in {c["name"] for c in pre_inspector.get_columns("user_agents")}

    pre_controls_cols = {c["name"] for c in pre_inspector.get_columns("conversation_agent_controls")}
    pre_queue_cols = {c["name"] for c in pre_inspector.get_columns("conversation_input_queue")}

    upgrade(engine, "head")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"agent_runs", "agent_worktrees", "task_board_items"} <= tables

    assert {c["name"] for c in inspector.get_columns("agent_runs")} == set(agent_runs.c.keys())
    assert {c["name"] for c in inspector.get_columns("agent_worktrees")} == set(agent_worktrees.c.keys())
    assert {c["name"] for c in inspector.get_columns("task_board_items")} == set(task_board_items.c.keys())
    assert {c["name"] for c in inspector.get_columns("user_agents")} == set(user_agents.c.keys())

    isolation_col = next(c for c in inspector.get_columns("user_agents") if c["name"] == "isolation")
    assert isolation_col["nullable"] is True

    assert inspector.get_pk_constraint("agent_runs")["constrained_columns"] == ["agent_id"]
    assert inspector.get_pk_constraint("agent_worktrees")["constrained_columns"] == ["agent_id"]
    assert inspector.get_pk_constraint("task_board_items")["constrained_columns"] == ["item_id"]

    tb_indexes = inspector.get_indexes("task_board_items")
    assert any(ix["column_names"] == ["conversation_id", "status"] for ix in tb_indexes)

    # 0063 tables must not be altered and their data must remain intact.
    assert {c["name"] for c in inspector.get_columns("conversation_agent_controls")} == pre_controls_cols
    assert {c["name"] for c in inspector.get_columns("conversation_input_queue")} == pre_queue_cols

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == TARGET_REVISION
        ua = conn.execute(
            text("SELECT name, memory, isolation FROM user_agents WHERE user_id = 'u-1'")
        ).mappings().one()
        assert ua["name"] == "researcher"
        assert ua["memory"] == "project"
        assert ua["isolation"] is None

        ctrl = conn.execute(
            text(
                "SELECT sender_kind, sender_agent_id, content FROM conversation_agent_controls "
                "WHERE control_id = 'ctrl-0063'"
            )
        ).mappings().one()
        assert ctrl["sender_kind"] == "parent"
        assert ctrl["sender_agent_id"] == "parent-0"
        assert ctrl["content"] == "Foque nos testes."

        queued = conn.execute(
            text(
                "SELECT origin, origin_conversation_id, content FROM conversation_input_queue "
                "WHERE queue_id = 'q-0063'"
            )
        ).mappings().one()
        assert queued["origin"] == "friend"
        assert queued["origin_conversation_id"] == "friend-conv-9"
        assert queued["content"] == "Continuar depois"


def test_migration_0066_is_idempotent_and_downgrades_preserving_data() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, PREVIOUS_REVISION)
    _seed_pre_0066_rows(engine)
    upgrade(engine, TARGET_REVISION)

    migration = importlib.import_module(
        "agentos.persistence.postgres.migrations.versions.0066_agent_runs_and_task_board"
    )

    # Running upgrade() again on an already upgraded database must be a no-op.
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

    assert {"agent_runs", "agent_worktrees", "task_board_items"} <= set(inspect(engine).get_table_names())
    assert "isolation" in {c["name"] for c in inspect(engine).get_columns("user_agents")}

    # Downgrade to 0065 drops the 3 tables and user_agents.isolation while preserving existing rows.
    downgrade(engine, PREVIOUS_REVISION)

    inspector = inspect(engine)
    tables_after = set(inspector.get_table_names())
    assert "agent_runs" not in tables_after
    assert "agent_worktrees" not in tables_after
    assert "task_board_items" not in tables_after
    assert "isolation" not in {c["name"] for c in inspector.get_columns("user_agents")}

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == PREVIOUS_REVISION
        ua = conn.execute(
            text("SELECT name, memory FROM user_agents WHERE user_id = 'u-1'")
        ).mappings().one()
        assert ua["name"] == "researcher"
        assert ua["memory"] == "project"
        assert (
            conn.execute(
                text("SELECT sender_kind FROM conversation_agent_controls WHERE control_id = 'ctrl-0063'")
            ).scalar()
            == "parent"
        )
        assert (
            conn.execute(
                text("SELECT origin FROM conversation_input_queue WHERE queue_id = 'q-0063'")
            ).scalar()
            == "friend"
        )

    # Re-upgrade works cleanly.
    upgrade(engine, TARGET_REVISION)
    assert {"agent_runs", "agent_worktrees", "task_board_items"} <= set(inspect(engine).get_table_names())
    assert "isolation" in {c["name"] for c in inspect(engine).get_columns("user_agents")}
