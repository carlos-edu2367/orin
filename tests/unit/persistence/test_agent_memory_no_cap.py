"""Tests ensuring removal of 300/500/1000 memory caps and paginated hybrid index sync (WS-D2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from agentos.memory.models_write import compute_text_hash
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, metadata


def test_no_cap_beyond_1000_user_and_300_friend_memories_and_paginated_hybrid_sync(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    rows = []
    # 1,120 active user memories: the oldest one (index 0) contains a unique needle token.
    for i in range(1120):
        ts = base + timedelta(seconds=i)
        fact = (
            "Fato rarissimo zanzibar_quasar_0000 sobre configuracao antiga"
            if i == 0
            else f"Registro operacional independente numero {i} da equipe alfa"
        )
        rows.append(
            {
                "memory_id": f"m_user_{i:04d}",
                "user_id": "u1",
                "scope_type": "user",
                "scope_id": "u1",
                "project_id": None,
                "fact": fact,
                "tags": [],
                "kind": "fact",
                "confidence": 0.9,
                "emotional_valence": 0.0,
                "salience": 0.5,
                "source": "user_explicit",
                "status": "active",
                "text_hash": compute_text_hash(fact),
                "valid_from": ts,
                "invalid_at": None,
                "source_kind": "user",
                "trust": 2,
                "hit_count": 0,
                "created_at": ts,
                "updated_at": ts,
            }
        )

    # 350 active friend memories (exceeds old 300 cap)
    for i in range(350):
        ts = base + timedelta(seconds=i)
        fact = f"Piada interna amigavel numero {i} do clube secreto"
        rows.append(
            {
                "memory_id": f"m_friend_{i:04d}",
                "user_id": "u1",
                "scope_type": "friend",
                "scope_id": "u1",
                "project_id": None,
                "fact": fact,
                "tags": [],
                "kind": "inside_joke",
                "confidence": 0.9,
                "emotional_valence": 0.4,
                "salience": 0.7,
                "source": "user_explicit",
                "status": "active",
                "text_hash": compute_text_hash(fact),
                "valid_from": ts,
                "invalid_at": None,
                "source_kind": "user",
                "trust": 2,
                "hit_count": 0,
                "created_at": ts,
                "updated_at": ts,
            }
        )

    with engine.begin() as conn:
        conn.execute(insert(agent_memories), rows)

    store = PostgresAgentMemoryStore(engine, "u1", data_dir=tmp_path)

    # _all() has no 500 cap, _all_active_user_rows() has no 1000 cap, _all_friend() has no 300 cap
    assert len(store._all()) == 1120
    assert len(store._all_active_user_rows()) == 1470
    assert len(store._all_friend()) == 350

    # Paginated iterator yields chunks of 500
    pages = list(store._iter_active_user_rows(page_size=500))
    assert [len(p) for p in pages] == [500, 500, 470]

    # Searching for the oldest memory (beyond old 500 and 1000 caps!) still finds it via hybrid index
    hits = store.search("zanzibar_quasar_0000", limit=5)
    assert hits
    assert hits[0]["memory_id"] == "m_user_0000"
