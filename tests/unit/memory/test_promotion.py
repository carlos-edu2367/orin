"""Unit tests for MemoryPromotionGate, concurrent scope locking, and 14-day proposal expiration (WS-D5 / D-RF10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading

from sqlalchemy import create_engine, select, update

from agentos.memory.agent_scope import AgentMemoryScope
from agentos.memory.models_write import MemoryWriteContext
from agentos.memory.promotion import PROPOSAL_EXPIRATION_DAYS, MemoryPromotionGate
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata
from agentos.workers.scheduler import _maybe_prune_activity_events


def _make_engine(tmp_path: Path, name: str = "promo.db"):
    engine = create_engine(
        f"sqlite:///{(tmp_path / name).as_posix()}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    metadata.create_all(engine)
    return engine


def test_proposal_stays_proposed_when_verification_fails_or_missing_and_promotes_on_pass(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "verdict.db")
    try:
        store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path)
        scope = AgentMemoryScope(
            user_id="user-1",
            project_id="proj-1",
            workspace_hash="ws1",
            agent_name="revisor-sql",
            mode="project",
        )
        ctx = MemoryWriteContext(
            source_kind="agent",
            actor="agent:revisor-sql",
            agent_key="revisor-sql",
            conversation_id="conv-1",
            turn_id="turn-42",
            trust=1,
        )
        saved = store.save(
            "Usar indices parciais em queries de faturamento.",
            ("sql",),
            scope_type="agent",
            agent_scope=scope,
            context=ctx,
        )
        mem_id = str(saved["memory_id"])
        assert saved["status"] == "proposed"

        gate = MemoryPromotionGate(engine, user_id="user-1")

        # 1) Missing verdict with learn_from_verified_only=True -> stays proposed
        res_none = gate.on_verified("turn-42", None, learn_from_verified_only=True)
        assert res_none.promoted_count == 0

        # 2) Failed verdict -> stays proposed
        res_fail = gate.on_verified("turn-42", "fail", learn_from_verified_only=True)
        assert res_fail.promoted_count == 0

        with engine.connect() as conn:
            row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
            assert row["status"] == "proposed"

        # 3) Approved verdict -> promoted to active + 'promoted' event recorded
        res_pass = gate.on_verified("turn-42", "pass", learn_from_verified_only=True)
        assert res_pass.promoted_count == 1
        assert res_pass.promoted_ids == (mem_id,)

        with engine.connect() as conn:
            row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
            events = [
                r["event"]
                for r in conn.execute(
                    select(memory_events)
                    .where(memory_events.c.memory_id == mem_id)
                    .order_by(memory_events.c.id.asc())
                ).mappings().all()
            ]
        assert row["status"] == "active"
        assert events == ["proposed", "promoted"]
    finally:
        engine.dispose()


def test_learn_from_verified_only_false_promotes_on_successful_turn_end_but_not_on_explicit_fail(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "unverified_ok.db")
    try:
        store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path)
        scope = AgentMemoryScope(
            user_id="user-1",
            project_id="proj-1",
            workspace_hash="ws1",
            agent_name="revisor-sql",
            mode="project",
        )
        ctx = MemoryWriteContext(
            source_kind="agent",
            actor="agent:revisor-sql",
            agent_key="revisor-sql",
            conversation_id="conv-1",
            turn_id="turn-unverified",
            trust=1,
        )
        saved = store.save(
            "Tabela invoices exige filtro por tenant_id.",
            scope_type="agent",
            agent_scope=scope,
            context=ctx,
            propose_only=True,
        )
        mem_id = str(saved["memory_id"])
        assert saved["status"] == "proposed"

        gate = MemoryPromotionGate(engine, user_id="user-1")
        # Explicit fail still blocks promotion even when learn_from_verified_only=False
        assert gate.on_verified("turn-unverified", False, learn_from_verified_only=False).promoted_count == 0
        # Successful turn end (verdict=None) promotes when learn_from_verified_only=False
        res = gate.on_verified("turn-unverified", None, learn_from_verified_only=False)
        assert res.promoted_ids == (mem_id,)
    finally:
        engine.dispose()


def test_concurrent_promotion_in_same_scope_with_barrier_never_duplicates(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "concurrent.db")
    try:
        store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path)
        scope = AgentMemoryScope(
            user_id="user-1",
            project_id="proj-1",
            workspace_hash="ws1",
            agent_name="revisor-sql",
            mode="project",
        )
        same_fact = "Sempre adicionar indice composto em (tenant_id, created_at)."
        # Two sibling subagents propose the exact same fact in the same scope
        s1 = store.save(
            same_fact,
            scope_type="agent",
            agent_scope=scope,
            context=MemoryWriteContext(
                source_kind="agent",
                actor="agent:revisor-sql",
                agent_key="revisor-sql",
                conversation_id="conv-1",
                turn_id="turn-child-1",
                trust=1,
            ),
        )
        s2 = store.save(
            same_fact,
            scope_type="agent",
            agent_scope=scope,
            context=MemoryWriteContext(
                source_kind="agent",
                actor="agent:revisor-sql",
                agent_key="revisor-sql",
                conversation_id="conv-1",
                turn_id="turn-child-2",
                trust=1,
            ),
        )
        assert s1["status"] == "proposed"
        assert s2["status"] == "proposed"

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        results: list = []

        def worker(turn_id: str) -> None:
            try:
                gate = MemoryPromotionGate(engine, user_id="user-1")
                barrier.wait(timeout=5)
                results.append(gate.on_verified(turn_id, True, learn_from_verified_only=True))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("turn-child-1",))
        t2 = threading.Thread(target=worker, args=("turn-child-2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Concurrent promotion raised: {errors}"
        assert len(results) == 2

        with engine.connect() as conn:
            active_rows = conn.execute(
                select(agent_memories).where(
                    agent_memories.c.user_id == "user-1",
                    agent_memories.c.scope_type == "agent",
                    agent_memories.c.agent_key == "revisor-sql",
                    agent_memories.c.status == "active",
                )
            ).mappings().all()
            all_rows = conn.execute(
                select(agent_memories).where(
                    agent_memories.c.user_id == "user-1",
                    agent_memories.c.scope_type == "agent",
                    agent_memories.c.agent_key == "revisor-sql",
                )
            ).mappings().all()

        assert len(active_rows) == 1
        assert len(all_rows) == 2
        statuses = sorted(str(r["status"]) for r in all_rows)
        assert statuses == ["active", "invalid"]
    finally:
        engine.dispose()


def test_expire_proposals_after_14_days_marks_invalid_records_expired_and_runs_in_scheduler(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "expire.db")
    try:
        assert PROPOSAL_EXPIRATION_DAYS == 14
        store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path)
        scope = AgentMemoryScope(
            user_id="user-1",
            project_id="proj-1",
            workspace_hash="ws1",
            agent_name="revisor-sql",
            mode="project",
        )
        old_prop = store.save(
            "Proposta antiga nao verificada.",
            scope_type="agent",
            agent_scope=scope,
            context=MemoryWriteContext(
                source_kind="agent",
                actor="agent:revisor-sql",
                agent_key="revisor-sql",
                conversation_id="conv-1",
                turn_id="turn-old",
                trust=1,
            ),
        )
        fresh_prop = store.save(
            "Proposta recente ainda dentro da janela.",
            scope_type="agent",
            agent_scope=scope,
            context=MemoryWriteContext(
                source_kind="agent",
                actor="agent:revisor-sql",
                agent_key="revisor-sql",
                conversation_id="conv-1",
                turn_id="turn-fresh",
                trust=1,
            ),
        )
        old_id = str(old_prop["memory_id"])
        fresh_id = str(fresh_prop["memory_id"])

        now = datetime.now(UTC)
        old_ts = now - timedelta(days=15)
        with engine.begin() as conn:
            conn.execute(
                update(agent_memories)
                .where(agent_memories.c.memory_id == old_id)
                .values(created_at=old_ts, updated_at=old_ts)
            )

        class _DummyChatStore:
            def prune_transient_activity_events(self, *, batch_size: int, max_batches: int) -> int:
                return 0

        gate = MemoryPromotionGate(engine)
        _maybe_prune_activity_events(
            _DummyChatStore(),  # type: ignore[arg-type]
            now_mono=100.0,
            next_prune_mono=0.0,
            promotion_gate=gate,
        )

        with engine.connect() as conn:
            old_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == old_id)).mappings().one()
            fresh_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == fresh_id)).mappings().one()
            old_events = [
                r["event"]
                for r in conn.execute(
                    select(memory_events)
                    .where(memory_events.c.memory_id == old_id)
                    .order_by(memory_events.c.id.asc())
                ).mappings().all()
            ]

        assert old_row["status"] == "invalid"
        assert old_row["invalid_at"] is not None
        assert "expired" in old_events
        assert fresh_row["status"] == "proposed"
    finally:
        engine.dispose()
