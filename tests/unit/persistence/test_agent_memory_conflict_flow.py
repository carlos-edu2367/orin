"""End-to-end persistence tests for PostgresAgentMemoryStore conflict flow (WS-D3 / D-RF6)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from sqlalchemy import create_engine, select

from agentos.agentic.events import AgentActivityEventType
from agentos.memory.conflict import ConflictResolver
from agentos.memory.models_write import MemoryWriteContext
from agentos.memory.policy import WritePolicyChain, default_write_policy
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata
from agentos.retrieval.models import EmbedderIdentity


def _store(tmp_path: Path, **kwargs):
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user:1", project_id="proj:orin", data_dir=tmp_path, **kwargs)
    return engine, store


class _FixedSimilarityEmbedder:
    """Deterministic embedder that yields controlled cosine similarity for conflict-band tests."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="synthetic-conflict", model="v1", dim=3)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for raw in texts:
            matched = [0.0, 0.0, 1.0]
            low = raw.lower()
            for key, vec in self._vectors.items():
                if key in low:
                    matched = vec
                    break
            out.append(matched)
        return out


def test_default_write_policy_includes_conflict_resolver() -> None:
    policy = default_write_policy()
    assert isinstance(policy, WritePolicyChain)
    assert any(isinstance(p, ConflictResolver) for p in policy.policies)
    assert AgentActivityEventType.MEMORY_CONFLICT == "memory.conflict"


def test_exact_normalized_hash_is_idempotent_noop_updating_last_used_at_without_event(tmp_path: Path) -> None:
    engine, store = _store(tmp_path)

    first = store.save("O projeto usa pnpm.", kind="operational", source="user_explicit")
    mem_id = str(first["memory_id"])

    with engine.connect() as conn:
        row_before = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == mem_id)
        ).mappings().one()
        events_before = conn.execute(
            select(memory_events).where(memory_events.c.memory_id == mem_id)
        ).mappings().all()

    assert row_before["last_used_at"] is None
    assert [e["event"] for e in events_before] == ["created"]

    # Normalized equivalent (different casing, spaces, trailing punctuation) -> NOOP
    dup = store.save("  o   projeto usa pnpm!  ", kind="operational", source="user_explicit")

    assert dup["created"] is False
    assert dup["memory_id"] == mem_id
    assert dup["superseded"] == []
    assert dup.get("decision") == "noop"

    with engine.connect() as conn:
        row_after = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == mem_id)
        ).mappings().one()
        events_after = conn.execute(
            select(memory_events).where(memory_events.c.memory_id == mem_id)
        ).mappings().all()

    assert row_after["last_used_at"] is not None
    assert len(events_after) == 1
    assert [e["event"] for e in events_after] == ["created"]


def test_criterion3_short_portuguese_replacement_supersedes_atomically_and_preserves_old_text(tmp_path: Path) -> None:
    engine, store = _store(tmp_path)

    first = store.save("o projeto usa pnpm", kind="operational", source="user_explicit")
    second = store.save("o projeto usa npm", kind="operational", source="user_explicit")

    old_id = str(first["memory_id"])
    new_id = str(second["memory_id"])
    assert old_id != new_id
    assert second["created"] is True
    assert second["superseded"] == [old_id]

    with engine.connect() as conn:
        old_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == old_id)).mappings().one()
        new_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == new_id)).mappings().one()

    assert old_row["status"] == "superseded"
    assert old_row["superseded_by"] == new_id
    assert old_row["invalid_at"] is not None

    assert new_row["status"] == "active"
    assert new_row["superseded_by"] is None
    assert new_row["invalid_at"] is None
    assert new_row["fact"] == "o projeto usa npm"

    old_history = store.lifecycle.history(old_id, "user:1")
    new_history = store.lifecycle.history(new_id, "user:1")
    assert [e["event"] for e in old_history] == ["created", "superseded"]
    assert old_history[1]["old_text"] == "o projeto usa pnpm"
    assert old_history[1]["new_text"] == "o projeto usa npm"
    assert old_history[1]["related_id"] == new_id
    assert [e["event"] for e in new_history] == ["created"]

    # Only the new memory is active
    active = store.recent(limit=10)
    assert [r["memory_id"] for r in active] == [new_id]


def test_criterion4_agent_contradicting_user_creates_proposed_and_cross_linked_conflict_events(
    tmp_path: Path,
    caplog,
) -> None:
    engine, store = _store(tmp_path)

    user_ctx = MemoryWriteContext(source_kind="user", actor="user", trust=2, conversation_id="conv-1", turn_id="t-1")
    user_saved = store.save("o projeto usa pnpm", kind="operational", context=user_ctx)
    user_id = str(user_saved["memory_id"])

    agent_ctx = MemoryWriteContext(
        source_kind="agent",
        actor="agent:coder",
        trust=1,
        conversation_id="conv-2",
        turn_id="t-2",
    )
    with caplog.at_level(logging.INFO, logger="agentos.persistence.postgres.agent_memory"):
        agent_saved = store.save("o projeto usa npm", kind="operational", context=agent_ctx)

    proposed_id = str(agent_saved["memory_id"])
    assert proposed_id != user_id
    assert agent_saved["created"] is True
    assert agent_saved["superseded"] == []
    assert agent_saved.get("status") == "proposed"
    assert agent_saved.get("decision") == "conflict"

    with engine.connect() as conn:
        old_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == user_id)).mappings().one()
        new_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == proposed_id)).mappings().one()

    # User memory stays active and untouched; agent memory is stored as proposed
    assert old_row["status"] == "active"
    assert old_row["superseded_by"] is None
    assert old_row["invalid_at"] is None
    assert new_row["status"] == "proposed"
    assert new_row["fact"] == "o projeto usa npm"

    # Active reads only see the user memory, never the proposed conflict
    active = store.recent(limit=10)
    assert [r["memory_id"] for r in active] == [user_id]

    # Both memories have cross-linked conflict events in memory_events
    old_events = store.lifecycle.history(user_id, "user:1")
    new_events = store.lifecycle.history(proposed_id, "user:1")

    assert [e["event"] for e in old_events] == ["created", "conflict"]
    assert old_events[1]["related_id"] == proposed_id
    assert old_events[1]["old_text"] == "o projeto usa pnpm"
    assert old_events[1]["new_text"] == "o projeto usa npm"

    assert [e["event"] for e in new_events] == ["conflict"]
    assert new_events[0]["related_id"] == user_id
    assert new_events[0]["new_text"] == "o projeto usa npm"

    # Structured daily metric log emitted for memory.conflict
    assert any("memory.conflict" in rec.getMessage() for rec in caplog.records)


def test_ambiguous_semantic_similarity_creates_proposed_conflict_when_llm_disabled(tmp_path: Path) -> None:
    # Cosine similarity between [1.0, 0.0, 0.0] and [0.90, 0.43589, 0.0] is ~0.90 (inside [0.85, 0.98))
    embedder = _FixedSimilarityEmbedder({
        "kubernetes": [1.0, 0.0, 0.0],
        "nomad": [0.90, 0.43589, 0.0],
    })
    engine, store = _store(tmp_path, embedder=embedder)

    ctx = MemoryWriteContext(source_kind="agent", actor="agent:ops", trust=1)
    first = store.save("A orquestracao em producao roda sobre cluster Kubernetes dedicado.", kind="architecture", context=ctx)
    second = store.save("Os servicos de producao sao agendados no ambiente Nomad corporativo.", kind="architecture", context=ctx)

    assert second["decision"] == "conflict"
    assert second["status"] == "proposed"
    assert second["superseded"] == []
    assert [r["memory_id"] for r in store.recent(limit=10)] == [first["memory_id"]]
