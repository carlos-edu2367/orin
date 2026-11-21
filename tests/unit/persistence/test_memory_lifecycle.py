"""Tests for MemoryLifecycle operations (WS-D2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, select

from agentos.memory.models_write import compute_text_hash, normalize_memory_text
from agentos.persistence.postgres.memory_lifecycle import MemoryLifecycle
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata


def _setup():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    return engine, MemoryLifecycle(engine)


def _insert_mem(engine, memory_id: str, fact: str, *, user_id: str = "u1", status: str = "active", now=None):
    ts = now or datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(agent_memories).values(
                memory_id=memory_id,
                user_id=user_id,
                scope_type="user",
                scope_id=user_id,
                project_id=None,
                fact=fact,
                tags=[],
                kind="fact",
                confidence=1.0,
                source="user_explicit",
                status=status,
                text_hash=compute_text_hash(fact),
                valid_from=ts,
                invalid_at=None if status == "active" else ts,
                source_kind="user",
                trust=2,
                hit_count=0,
                created_at=ts,
                updated_at=ts,
            )
        )


def test_normalize_and_compute_text_hash() -> None:
    assert normalize_memory_text("  O Projeto usa   pnpm!  ") == "o projeto usa pnpm"
    assert normalize_memory_text("o projeto usa pnpm...") == "o projeto usa pnpm"
    h1 = compute_text_hash("O projeto usa pnpm.")
    h2 = compute_text_hash("  o   PROJETO usa pnpm! ")
    assert h1 == h2
    assert len(h1) == 64


def test_supersede_atomic_transaction_and_chain_resolution() -> None:
    engine, lc = _setup()
    _insert_mem(engine, "m1", "o projeto usa npm")
    _insert_mem(engine, "m2", "o projeto usa yarn")
    _insert_mem(engine, "m3", "o projeto usa pnpm")

    with engine.begin() as conn:
        assert lc.supersede(conn, "m1", "m2", actor="agent:dev", old_text="o projeto usa npm", new_text="o projeto usa yarn", user_id="u1") is True
        assert lc.supersede(conn, "m2", "m3", actor="user", old_text="o projeto usa yarn", new_text="o projeto usa pnpm", user_id="u1") is True

    # Active successor follows full chain m1 -> m2 -> m3
    succ = lc.get_active_successor("m1", "u1")
    assert succ is not None
    assert succ["memory_id"] == "m3"
    assert succ["fact"] == "o projeto usa pnpm"

    # Strict user isolation
    assert lc.get_active_successor("m1", "u2") is None

    # Old rows have status='superseded' and invalid_at set
    with engine.connect() as conn:
        m1 = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == "m1")).mappings().one()
        assert m1["status"] == "superseded"
        assert m1["superseded_by"] == "m2"
        assert m1["invalid_at"] is not None

    # History preserves old_text and related_id
    hist = lc.history("m1", "u1")
    assert len(hist) == 1
    assert hist[0]["event"] == "superseded"
    assert hist[0]["old_text"] == "o projeto usa npm"
    assert hist[0]["new_text"] == "o projeto usa yarn"
    assert hist[0]["related_id"] == "m2"
    assert hist[0]["actor"] == "agent:dev"


def test_tombstone_clears_fact_keeps_hash_never_logs_old_text_and_restore_rules() -> None:
    engine, lc = _setup()
    _insert_mem(engine, "m1", "meu cpf ficticio para teste")
    expected_hash = compute_text_hash("meu cpf ficticio para teste")

    assert lc.tombstone("m1", "u1", actor="user") is True
    # Tombstoning twice returns False
    assert lc.tombstone("m1", "u1", actor="user") is False

    with engine.connect() as conn:
        row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == "m1")).mappings().one()
        assert row["status"] == "deleted"
        assert row["fact"] == ""
        assert row["text_hash"] == expected_hash
        assert row["invalid_at"] is not None

        ev = conn.execute(select(memory_events).where(memory_events.c.memory_id == "m1")).mappings().one()
        assert ev["event"] == "forgotten"
        assert ev["old_text"] is None
        assert ev["new_text"] is None

    # Restoring a tombstone without text returns structured error
    res_err = lc.restore("m1", "u1")
    assert res_err["ok"] is False
    assert res_err["error"] == "tombstone_text_erased"

    # Restoring a superseded memory (which still has fact) reactivates it
    _insert_mem(engine, "m2", "usar pytest -q")
    _insert_mem(engine, "m3", "usar pytest -v")
    with engine.begin() as conn:
        lc.supersede(conn, "m2", "m3", actor="user", user_id="u1")

    res_ok = lc.restore("m2", "u1", actor="user")
    assert res_ok["ok"] is True
    assert res_ok["status"] == "active"
    with engine.connect() as conn:
        m2 = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == "m2")).mappings().one()
        assert m2["status"] == "active"
        assert m2["superseded_by"] is None
        assert m2["invalid_at"] is None


def test_purge_tombstones_removes_only_expired_deleted_rows() -> None:
    engine, lc = _setup()
    now = datetime.now(UTC)
    old_ts = now - timedelta(days=120)
    recent_ts = now - timedelta(days=10)

    _insert_mem(engine, "m_old_del", "antiga", now=old_ts)
    _insert_mem(engine, "m_new_del", "recente", now=recent_ts)
    _insert_mem(engine, "m_old_active", "ativa antiga", now=old_ts)

    lc.tombstone("m_old_del", "u1", now=old_ts)
    lc.tombstone("m_new_del", "u1", now=recent_ts)

    purged = lc.purge_tombstones(now - timedelta(days=90))
    assert purged == 1

    with engine.connect() as conn:
        remaining = {r["memory_id"] for r in conn.execute(select(agent_memories.c.memory_id)).mappings()}
    assert remaining == {"m_new_del", "m_old_active"}
