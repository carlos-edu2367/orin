"""Tests for memory tombstones, auto-learning suppression, explicit user recreate, and scheduler purge (WS-D2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select

from agentos.memory.models_write import MemoryWriteContext, compute_text_hash
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata
from agentos.projects.store import PostgresProjectStore
from agentos.workers.scheduler import _maybe_prune_activity_events


def test_forget_creates_tombstone_blocks_mechanical_relearn_and_allows_explicit_user_recreate(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user:1", data_dir=tmp_path)

    fact = "O servidor de staging usa porta 8081."
    saved = store.save(fact, kind="operational", source="user_explicit")
    mem_id = str(saved["memory_id"])

    assert store.forget(mem_id) is True

    # Row still exists as tombstone with fact="" and preserved text_hash; forgotten event has old_text=None
    with engine.connect() as conn:
        row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
        events = conn.execute(
            select(memory_events)
            .where(memory_events.c.memory_id == mem_id)
            .order_by(memory_events.c.id)
        ).mappings().all()

    assert row["status"] == "deleted"
    assert row["fact"] == ""
    assert row["text_hash"] == compute_text_hash(fact)
    assert row["invalid_at"] is not None
    assert events[-1]["event"] == "forgotten"
    assert events[-1]["old_text"] is None

    # get_memory on deleted returns clear "memória esquecida" marker
    loaded = store.get_memory(mem_id)
    assert loaded is not None
    assert loaded["status"] == "deleted"
    assert "memória esquecida" in str(loaded.get("error") or loaded.get("message") or "").lower()

    # Mechanical / consolidation learning with same normalized text MUST NOT recreate
    mech = store.save(
        "o servidor de staging usa porta 8081",
        kind="operational",
        source="mechanical",
        context=MemoryWriteContext(source_kind="agent", actor="system", trust=1),
    )
    assert mech["created"] is False
    assert mech.get("blocked_by_tombstone") is True

    cons = store.save(
        "O SERVIDOR DE STAGING USA PORTA 8081!",
        kind="operational",
        source="consolidation",
        context=MemoryWriteContext(source_kind="consolidator", actor="consolidator", trust=1),
    )
    assert cons["created"] is False
    assert cons.get("blocked_by_tombstone") is True
    assert store.recent(limit=10) == []

    # Explicit user Remember (source_kind="user") RECREATES the memory
    recreated = store.save(
        fact,
        kind="operational",
        source="user_explicit",
        context=MemoryWriteContext(source_kind="user", actor="user", trust=2),
    )
    assert recreated["created"] is True
    assert recreated["memory_id"] != mem_id
    active = store.recent(limit=10)
    assert [item["fact"] for item in active] == [fact]


def test_project_store_delete_memory_uses_tombstone_instead_of_physical_delete(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    proj_store = PostgresProjectStore(engine)
    project = proj_store.create(user_id="user:1", name="Orin", description="Test")
    mem_store = PostgresAgentMemoryStore(engine, "user:1", project_id=project.project_id, data_dir=tmp_path)

    saved = mem_store.save("Usar uv run python nos scripts.", scope_type="project")
    mem_id = str(saved["memory_id"])

    assert proj_store.delete_memory(project.project_id, "user:1", mem_id, scope="project") is True
    # Already tombstoned -> second delete returns False
    assert proj_store.delete_memory(project.project_id, "user:1", mem_id, scope="project") is False

    # Not listed anymore as active
    assert proj_store.list_memories(project.project_id, "user:1") == []

    # Physically still present as tombstone with fact=""
    with engine.connect() as conn:
        row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
    assert row["status"] == "deleted"
    assert row["fact"] == ""


def test_scheduler_prunes_expired_tombstones_respecting_retention_days(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user:1", data_dir=tmp_path)

    old_saved = store.save("Memoria antiga esquecida ha 100 dias.")
    recent_saved = store.save("Memoria recente esquecida ha 5 dias.")
    now = datetime.now(UTC)

    store.lifecycle.tombstone(str(old_saved["memory_id"]), "user:1", now=now - timedelta(days=100))
    store.lifecycle.tombstone(str(recent_saved["memory_id"]), "user:1", now=now - timedelta(days=5))

    class _FakeChatStore:
        def prune_transient_activity_events(self, *, batch_size: int, max_batches: int) -> int:
            return 0

    _maybe_prune_activity_events(
        _FakeChatStore(),
        now_mono=100.0,
        next_prune_mono=0.0,
        memory_lifecycle=store.lifecycle,
        tombstone_retention_days=90,
    )

    with engine.connect() as conn:
        remaining = {r["memory_id"] for r in conn.execute(select(agent_memories.c.memory_id)).mappings()}
    assert str(old_saved["memory_id"]) not in remaining
    assert str(recent_saved["memory_id"]) in remaining
