"""Migration 0067_pipeline_runs: durable journal tables (WS-C3)."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import metadata

REVISION = "0067_pipeline_runs"
PREVIOUS = "0066_agent_runs_and_task_board"

RUN_COLUMNS = {
    "run_id", "user_id", "conversation_id", "workspace_root", "pipeline_id", "definition_hash", "source", "inputs",
    "state", "used_tokens", "used_cost_usd", "used_seconds", "started_at", "finished_at",
    "definition_snapshot", "turn_id", "stop_reason",
}
STEP_COLUMNS = {
    "run_id", "step_id", "item_key", "attempt", "input_hash", "state", "agent_id", "output", "error", "loop_count",
    "input_tokens", "output_tokens", "cost_usd", "started_at", "finished_at",
}


def _engine(tmp_path: Path):
    return create_engine(f"sqlite:///{(tmp_path / 'orin.db').as_posix()}")


def _head(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("select version_num from alembic_version")).scalar()


def test_upgrade_creates_both_tables_with_columns_keys_and_indexes(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    upgrade(engine, PREVIOUS)
    assert "pipeline_runs" not in inspect(engine).get_table_names()
    upgrade(engine, REVISION)
    inspector = inspect(engine)
    assert {"pipeline_runs", "pipeline_step_runs"} <= set(inspector.get_table_names())
    assert {c["name"] for c in inspector.get_columns("pipeline_runs")} == RUN_COLUMNS
    assert {c["name"] for c in inspector.get_columns("pipeline_step_runs")} == STEP_COLUMNS
    assert inspector.get_pk_constraint("pipeline_runs")["constrained_columns"] == ["run_id"]
    assert inspector.get_pk_constraint("pipeline_step_runs")["constrained_columns"] == [
        "run_id", "step_id", "item_key", "attempt",
    ]
    run_indexes = {ix["name"]: ix["column_names"] for ix in inspector.get_indexes("pipeline_runs")}
    assert run_indexes["ix_pipeline_runs_conversation_started"][0] == "conversation_id"
    assert run_indexes["ix_pipeline_runs_user_state"] == ["user_id", "state"]
    step_indexes = {ix["name"]: ix["column_names"] for ix in inspector.get_indexes("pipeline_step_runs")}
    assert step_indexes["ix_pipeline_step_runs_reuse"] == ["run_id", "step_id", "loop_count"]
    assert step_indexes["ix_pipeline_step_runs_agent"] == ["agent_id"]
    assert _head(engine) == REVISION


def test_upgrade_is_idempotent_when_the_store_already_created_the_tables(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    upgrade(engine, PREVIOUS)
    PostgresChatStore(engine)  # metadata.create_all patches in the new tables
    metadata.create_all(engine)
    assert "pipeline_runs" in inspect(engine).get_table_names()
    upgrade(engine, REVISION)
    upgrade(engine, REVISION)
    assert _head(engine) == REVISION
    assert {c["name"] for c in inspect(engine).get_columns("pipeline_runs")} == RUN_COLUMNS


def test_downgrade_removes_the_tables_and_the_chain_down_to_0060_climbs_back(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    upgrade(engine, REVISION)
    downgrade(engine, PREVIOUS)
    names = set(inspect(engine).get_table_names())
    assert "pipeline_runs" not in names and "pipeline_step_runs" not in names
    assert _head(engine) == PREVIOUS
    upgrade(engine, REVISION)
    downgrade(engine, "0060_discord_bridge_and_session_watches")
    names = set(inspect(engine).get_table_names())
    assert "pipeline_runs" not in names and "pipeline_step_runs" not in names
    upgrade(engine, REVISION)
    assert {"pipeline_runs", "pipeline_step_runs"} <= set(inspect(engine).get_table_names())
    assert _head(engine) == REVISION


def test_existing_data_survives_the_upgrade(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    upgrade(engine, PREVIOUS)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO conversation_agents (agent_id, conversation_id, user_id, name, role, state, created_at, updated_at) "
            "VALUES ('a1', 'c1', 'u1', 'Ana', 'dev', 'idle', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ))
    upgrade(engine, REVISION)
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from conversation_agents where agent_id = 'a1'")).scalar() == 1
    assert _head(engine) == REVISION


def test_schema_metadata_declares_the_journal_tables() -> None:
    assert {c.name for c in metadata.tables["pipeline_runs"].columns} == RUN_COLUMNS
    assert {c.name for c in metadata.tables["pipeline_step_runs"].columns} == STEP_COLUMNS
    assert isinstance(metadata.tables["pipeline_step_runs"].c.loop_count.type, sa.SmallInteger)
