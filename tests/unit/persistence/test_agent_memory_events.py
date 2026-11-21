"""Tests for memory_events audit trail, near-duplicate supersession, and get_memory chain (WS-D2)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select

from agentos.memory.models_write import MemoryWriteContext
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata


def _store(tmp_path: Path, **kwargs) -> tuple:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user:1", project_id="proj:orin", data_dir=tmp_path, **kwargs)
    return engine, store


def test_near_duplicate_supersedes_instead_of_overwriting_and_preserves_old_text(tmp_path: Path) -> None:
    engine, store = _store(tmp_path)

    old_fact = (
        "No repositorio Orin executamos sempre a suite completa de testes unitarios "
        "com pytest passando a flag no cacheprovider no ambiente Windows local"
    )
    new_fact = (
        "No repositorio Orin executamos sempre a suite completa de testes unitarios "
        "com pytest passando a flag no cacheprovider no ambiente Windows"
    )

    first = store.save(old_fact, ("pytest",), kind="operational", confidence=0.7)
    second = store.save(new_fact, ("ci",), kind="operational", confidence=0.95)

    assert first["created"] is True
    assert second["created"] is True
    assert second["memory_id"] != first["memory_id"]
    assert second["superseded"] == [first["memory_id"]]

    # Old row stays superseded with invalid_at set; new row is active with valid_from and merged tags
    with engine.connect() as conn:
        old_row = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == str(first["memory_id"]))
        ).mappings().one()
        new_row = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == str(second["memory_id"]))
        ).mappings().one()

    assert old_row["status"] == "superseded"
    assert old_row["superseded_by"] == second["memory_id"]
    assert old_row["invalid_at"] is not None
    assert new_row["status"] == "active"
    assert new_row["superseded_by"] is None
    assert new_row["invalid_at"] is None
    assert new_row["valid_from"] is not None

    # memory_events recorded created + superseded (preserving old_text) + created
    history_old = store.lifecycle.history(str(first["memory_id"]), "user:1")
    assert [e["event"] for e in history_old] == ["created", "superseded"]
    assert history_old[1]["old_text"] == old_fact
    assert history_old[1]["new_text"] == new_fact
    assert history_old[1]["related_id"] == second["memory_id"]

    history_new = store.lifecycle.history(str(second["memory_id"]), "user:1")
    assert [e["event"] for e in history_new] == ["created"]
    assert history_new[0]["new_text"] == new_fact

    # get_memory(old_id) returns the active successor with superseded_from=<old_id>
    loaded = store.get_memory(str(first["memory_id"]))
    assert loaded is not None
    assert loaded["memory_id"] == second["memory_id"]
    assert loaded["fact"] == new_fact
    assert loaded["superseded_from"] == first["memory_id"]
    assert loaded["status"] == "active"
    assert loaded["source_kind"] == "user"
    assert loaded["trust"] == 2
    assert loaded["valid_from"] is not None


def test_save_with_explicit_context_records_provenance_and_event(tmp_path: Path) -> None:
    engine, store = _store(tmp_path)
    ctx = MemoryWriteContext(
        source_kind="agent",
        source_ref="conv-99/msg-4",
        actor="agent:revisor-sql",
        agent_key="revisor-sql",
        conversation_id="conv-99",
        turn_id="turn-7",
        trust=1,
    )
    saved = store.save("Sempre usar indices parciais em tabelas grandes.", scope_type="agent", context=ctx)
    assert saved["created"] is True

    with engine.connect() as conn:
        row = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == str(saved["memory_id"]))
        ).mappings().one()
        ev = conn.execute(
            select(memory_events).where(memory_events.c.memory_id == str(saved["memory_id"]))
        ).mappings().one()

    assert row["scope_type"] == "agent"
    assert row["agent_key"] == "revisor-sql"
    assert row["status"] == "proposed"
    assert row["source_kind"] == "agent"
    assert row["source_ref"] == "conv-99/msg-4"
    assert row["trust"] == 1
    assert ev["event"] == "proposed"
    assert ev["actor"] == "agent:revisor-sql"
    assert ev["conversation_id"] == "conv-99"
    assert ev["turn_id"] == "turn-7"
