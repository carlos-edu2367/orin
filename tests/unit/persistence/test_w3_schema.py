from __future__ import annotations

from sqlalchemy import create_engine, inspect

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
