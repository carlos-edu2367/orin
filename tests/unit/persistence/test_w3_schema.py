from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import (
    conversation_compact_boundaries,
    conversation_input_queue,
    conversation_prompt_snapshots,
    conversation_sessions,
)

W3_TABLES = (conversation_input_queue, conversation_prompt_snapshots, conversation_compact_boundaries, conversation_sessions)


def test_w3_tables_and_columns_exist_after_migrations() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert {table.name for table in W3_TABLES} <= names
    assert "turn_kind" in {column["name"] for column in inspector.get_columns("conversation_turns")}
    assert "lease_renewed_at" in {column["name"] for column in inspector.get_columns("conversation_dispatches")}


def test_the_migration_matches_the_metadata_columns() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    inspector = inspect(engine)
    for table in W3_TABLES:
        assert {column["name"] for column in inspector.get_columns(table.name)} == {column.name for column in table.columns}


def test_the_store_patches_a_database_that_predates_w3() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, "0047_conversation_mode_and_permission")
    PostgresChatStore(engine)
    inspector = inspect(engine)
    assert {table.name for table in W3_TABLES} <= set(inspector.get_table_names())
    assert "turn_kind" in {column["name"] for column in inspector.get_columns("conversation_turns")}
    assert "lease_renewed_at" in {column["name"] for column in inspector.get_columns("conversation_dispatches")}


def test_the_migration_reaches_head_after_the_store_patched_the_database(tmp_path: Path) -> None:
    # A database that skipped migrations, self-patched by the store, must still take 0048 (it creates only what is missing).
    engine = create_engine(f"sqlite:///{(tmp_path / 'orin.db').as_posix()}")
    upgrade(engine, "0047_conversation_mode_and_permission")
    PostgresChatStore(engine)
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0049_w4_tools"
    inspector = inspect(engine)
    assert {table.name for table in W3_TABLES} <= set(inspector.get_table_names())
    assert "turn_kind" in {column["name"] for column in inspector.get_columns("conversation_turns")}
    assert "lease_renewed_at" in {column["name"] for column in inspector.get_columns("conversation_dispatches")}


def test_the_store_starts_on_a_database_that_is_already_at_head(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'orin.db').as_posix()}")
    upgrade(engine)
    PostgresChatStore(engine)
    assert {table.name for table in W3_TABLES} <= set(inspect(engine).get_table_names())


def test_the_store_adds_the_idempotency_key_to_an_older_queue_table(tmp_path: Path) -> None:
    # A queue table created before the idempotency key existed gets the column and its unique index at start.
    engine = create_engine(f"sqlite:///{(tmp_path / 'orin.db').as_posix()}")
    upgrade(engine, "0047_conversation_mode_and_permission")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE conversation_input_queue (id INTEGER PRIMARY KEY, queue_id VARCHAR(255) NOT NULL UNIQUE, "
            "conversation_id VARCHAR(255) NOT NULL, user_id VARCHAR(255) NOT NULL, content VARCHAR(16000) NOT NULL, "
            "state VARCHAR(16) NOT NULL, turn_id VARCHAR(255), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        ))
    PostgresChatStore(engine)
    inspector = inspect(engine)
    assert "idempotency_key" in {column["name"] for column in inspector.get_columns("conversation_input_queue")}
    assert "ux_conversation_input_queue_idempotency" in {index["name"] for index in inspector.get_indexes("conversation_input_queue")}
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0049_w4_tools"
