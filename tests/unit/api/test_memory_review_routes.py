from __future__ import annotations

import asyncio
import gc
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, insert, select, update

from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemoryClientEventStream,
    InMemorySecurityService,
    create_app,
)
from agentos.memory.models_write import MemoryWriteContext, compute_text_hash
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.memory_lifecycle import MemoryLifecycle
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import agent_memories, memory_events
from agentos.projects.store import PostgresProjectStore

AUTH_U1 = {"Authorization": "Bearer pat-u1"}
AUTH_U2 = {"Authorization": "Bearer pat-u2"}


def _mut_headers(token: str = "pat-u1", key: str = "k-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


def _make_engine(tmp_path: Path, name: str = "review_routes.db"):
    engine = create_engine(
        f"sqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"check_same_thread": False},
    )
    upgrade(engine, "head")
    return engine


def _make_app(engine):
    security = InMemorySecurityService()
    security.add_pat("pat-u1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    security.add_pat("pat-u2", AuthenticatedPrincipal("user-2", "cred-2", frozenset({"api"})))
    services = ApiServices(
        security=security,
        events=InMemoryClientEventStream(),
        projects=PostgresProjectStore(engine),
    )
    return create_app(services)


def _insert_memory(
    engine,
    *,
    memory_id: str,
    user_id: str,
    fact: str,
    status: str = "active",
    scope_type: str = "user",
    scope_id: str | None = None,
    project_id: str | None = None,
    agent_key: str | None = None,
    source_kind: str = "agent",
    superseded_by: str | None = None,
    text_hash: str | None = None,
) -> str:
    now = datetime.now(UTC)
    resolved_scope_id = scope_id if scope_id is not None else (project_id or user_id)
    with engine.begin() as conn:
        conn.execute(
            insert(agent_memories).values(
                memory_id=memory_id,
                user_id=user_id,
                conversation_id=None,
                scope_type=scope_type,
                scope_id=resolved_scope_id,
                project_id=project_id,
                source_message_id=None,
                source_execution_id=None,
                fact=fact,
                tags=[],
                kind="fact",
                confidence=1.0,
                emotional_valence=0.0,
                salience=0.5,
                source="agent_learned",
                hit_count=0,
                last_used_at=None,
                superseded_by=superseded_by,
                status=status,
                agent_key=agent_key,
                text_hash=text_hash if text_hash is not None else compute_text_hash(fact or memory_id),
                valid_from=now,
                invalid_at=now if status in {"superseded", "invalid", "deleted"} else None,
                source_kind=source_kind,
                source_ref=None,
                trust=1,
                created_at=now,
                updated_at=now,
            )
        )
    return memory_id


def test_review_list_filters_and_pairs_conflicts_with_user_isolation(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "list.db")
    try:
        lifecycle = MemoryLifecycle(engine)
        # Active memory for user-1
        _insert_memory(engine, memory_id="mem_active_u1", user_id="user-1", fact="O projeto usa pnpm.", status="active", project_id="proj-1", scope_type="project")
        # Proposed conflict memory paired with mem_active_u1
        _insert_memory(engine, memory_id="mem_conflict_u1", user_id="user-1", fact="O projeto usa npm.", status="proposed", project_id="proj-1", scope_type="project")
        with engine.begin() as conn:
            lifecycle.record_event(
                conn,
                memory_id="mem_conflict_u1",
                user_id="user-1",
                event="conflict",
                actor="agent:main",
                new_text="O projeto usa npm.",
                related_id="mem_active_u1",
            )
        # Quarantined memory from web tool
        _insert_memory(engine, memory_id="mem_quar_u1", user_id="user-1", fact="Snippet da web.", status="quarantined", source_kind="web")
        # Agent-scoped proposed memory
        _insert_memory(engine, memory_id="mem_agent_u1", user_id="user-1", fact="Indice SQL composto ajuda.", status="proposed", scope_type="agent", project_id="proj-1", agent_key="revisor-sql")
        # Active agent-scoped memory
        _insert_memory(engine, memory_id="mem_agent_active_u1", user_id="user-1", fact="Evitar SELECT *.", status="active", scope_type="agent", project_id="proj-1", agent_key="revisor-sql")
        # History tombstoned memory
        _insert_memory(engine, memory_id="mem_del_u1", user_id="user-1", fact="", status="deleted", text_hash="h-del")
        # Another user's pending memory — MUST NEVER leak to user-1
        _insert_memory(engine, memory_id="mem_other_u2", user_id="user-2", fact="Segredo do user-2.", status="proposed", agent_key="outro-agente")

        client = TestClient(_make_app(engine))

        # Default GET /v1/memory/review -> pending items (proposed + quarantined) of user-1 only
        resp = client.get("/v1/memory/review", headers=AUTH_U1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = {item["memory_id"] for item in body["items"]}
        assert ids == {"mem_conflict_u1", "mem_quar_u1", "mem_agent_u1"}
        assert "mem_other_u2" not in ids
        assert "revisor-sql" in body["agent_keys"]
        assert "outro-agente" not in body["agent_keys"]

        conflict_item = next(item for item in body["items"] if item["memory_id"] == "mem_conflict_u1")
        assert conflict_item["review_kind"] == "conflict"
        assert conflict_item["conflicting_memory"] is not None
        assert conflict_item["conflicting_memory"]["memory_id"] == "mem_active_u1"
        assert conflict_item["conflicting_memory"]["fact"] == "O projeto usa pnpm."

        quar_item = next(item for item in body["items"] if item["memory_id"] == "mem_quar_u1")
        assert quar_item["review_kind"] == "quarantined"

        # Filter by agent_key on active tab
        resp_active = client.get("/v1/memory/review?status=active&agent_key=revisor-sql", headers=AUTH_U1)
        assert resp_active.status_code == 200
        assert [item["memory_id"] for item in resp_active.json()["items"]] == ["mem_agent_active_u1"]

        # History tab includes deleted/superseded/invalid with can_restore flag
        resp_hist = client.get("/v1/memory/review?status=history", headers=AUTH_U1)
        assert resp_hist.status_code == 200
        hist_items = resp_hist.json()["items"]
        assert [item["memory_id"] for item in hist_items] == ["mem_del_u1"]
        assert hist_items[0]["can_restore"] is False
    finally:
        engine.dispose()


def test_approve_promotes_proposed_and_quarantined_and_reject_marks_invalid(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "approve_reject.db")
    try:
        _insert_memory(engine, memory_id="mem_prop", user_id="user-1", fact="Usar pytest -q.", status="proposed", scope_type="agent", agent_key="revisor")
        _insert_memory(engine, memory_id="mem_quar", user_id="user-1", fact="Fato vindo de WebFetch.", status="quarantined", source_kind="web")
        _insert_memory(engine, memory_id="mem_rej", user_id="user-1", fact="Fato incorreto.", status="proposed")

        client = TestClient(_make_app(engine))

        # Cross-user approve must fail with 404
        cross = client.post("/v1/memory/mem_prop/approve", headers=_mut_headers("pat-u2", "k-x"))
        assert cross.status_code == 404

        # Approve proposed -> active + event 'approved'
        r1 = client.post("/v1/memory/mem_prop/approve", headers=_mut_headers("pat-u1", "k-1"))
        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "active"
        assert r1.json()["memory"]["status"] == "active"

        # Approve quarantined -> active + event 'approved'
        r2 = client.post("/v1/memory/mem_quar/approve", headers=_mut_headers("pat-u1", "k-2"))
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "active"

        # Reject -> invalid + invalid_at + event 'rejected'
        r3 = client.post("/v1/memory/mem_rej/reject", headers=_mut_headers("pat-u1", "k-3"))
        assert r3.status_code == 200, r3.text
        assert r3.json()["status"] == "invalid"
        assert r3.json()["memory"]["invalid_at"] is not None

        lifecycle = MemoryLifecycle(engine)
        assert [e["event"] for e in lifecycle.history("mem_prop", "user-1")] == ["approved"]
        assert [e["event"] for e in lifecycle.history("mem_quar", "user-1")] == ["approved"]
        assert [e["event"] for e in lifecycle.history("mem_rej", "user-1")] == ["rejected"]
    finally:
        engine.dispose()


def test_conflict_resolutions_keep_new_keep_old_keep_both(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "conflicts.db")
    try:
        lifecycle = MemoryLifecycle(engine)

        def _seed_pair(prefix: str, old_fact: str, new_fact: str) -> tuple[str, str]:
            old_id = _insert_memory(engine, memory_id=f"{prefix}_old", user_id="user-1", fact=old_fact, status="active")
            new_id = _insert_memory(engine, memory_id=f"{prefix}_new", user_id="user-1", fact=new_fact, status="proposed")
            with engine.begin() as conn:
                lifecycle.record_event(conn, memory_id=new_id, user_id="user-1", event="conflict", actor="agent", new_text=new_fact, related_id=old_id)
                lifecycle.record_event(conn, memory_id=old_id, user_id="user-1", event="conflict", actor="agent", old_text=old_fact, new_text=new_fact, related_id=new_id)
            return old_id, new_id

        old1, new1 = _seed_pair("c1", "Usar yarn no repo.", "Usar pnpm no repo.")
        old2, new2 = _seed_pair("c2", "Banco principal Postgres.", "Banco principal MySQL.")
        old3, new3 = _seed_pair("c3", "Deploy via Docker.", "Deploy via Fly.io.")

        client = TestClient(_make_app(engine))

        # 1) keep_new ("Ficar com a nova"): new becomes active, old is atomically superseded
        r_new = client.post(
            f"/v1/memory/{new1}/approve",
            headers=_mut_headers("pat-u1", "k-c1"),
            json={"resolution": "keep_new"},
        )
        assert r_new.status_code == 200, r_new.text
        assert r_new.json()["status"] == "active"
        assert r_new.json()["superseded_memory_id"] == old1
        with engine.connect() as conn:
            old1_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == old1)).mappings().one()
            new1_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == new1)).mappings().one()
        assert old1_row["status"] == "superseded"
        assert old1_row["superseded_by"] == new1
        assert old1_row["invalid_at"] is not None
        assert new1_row["status"] == "active"
        assert "superseded" in [e["event"] for e in lifecycle.history(old1, "user-1")]

        # 2) keep_old ("Ficar com a antiga"): new becomes invalid (rejected), old remains active
        r_old = client.post(
            f"/v1/memory/{new2}/resolve-conflict",
            headers=_mut_headers("pat-u1", "k-c2"),
            json={"resolution": "keep_old"},
        )
        assert r_old.status_code == 200, r_old.text
        assert r_old.json()["status"] == "invalid"
        with engine.connect() as conn:
            old2_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == old2)).mappings().one()
            new2_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == new2)).mappings().one()
        assert old2_row["status"] == "active"
        assert new2_row["status"] == "invalid"
        assert new2_row["invalid_at"] is not None
        assert "rejected" in [e["event"] for e in lifecycle.history(new2, "user-1")]

        # 3) keep_both ("Manter as duas"): both remain active and resolution recorded in events
        r_both = client.post(
            f"/v1/memory/{new3}/approve",
            headers=_mut_headers("pat-u1", "k-c3"),
            json={"resolution": "keep_both"},
        )
        assert r_both.status_code == 200, r_both.text
        assert r_both.json()["status"] == "active"
        with engine.connect() as conn:
            old3_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == old3)).mappings().one()
            new3_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == new3)).mappings().one()
        assert old3_row["status"] == "active"
        assert new3_row["status"] == "active"
        assert "approved" in [e["event"] for e in lifecycle.history(new3, "user-1")]
        assert "approved" in [e["event"] for e in lifecycle.history(old3, "user-1")]
    finally:
        engine.dispose()


def test_restore_reactivates_with_text_and_returns_409_for_erased_tombstone(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "restore.db")
    try:
        store = PostgresAgentMemoryStore(engine, user_id="user-1")
        saved = store.save("Segredo que sera esquecido.", kind="fact", source="user_explicit")
        forgotten_id = str(saved["memory_id"])
        assert store.forget(forgotten_id) is True

        # Superseded / invalid memory with preserved text CAN be restored
        _insert_memory(engine, memory_id="mem_inv", user_id="user-1", fact="Prefiro respostas diretas.", status="invalid")

        client = TestClient(_make_app(engine))

        # Erased tombstone -> HTTP 409 with clear pt-BR message
        r_tomb = client.post(f"/v1/memory/{forgotten_id}/restore", headers=_mut_headers("pat-u1", "k-t1"))
        assert r_tomb.status_code == 409, r_tomb.text
        err_text = r_tomb.text.lower()
        assert "texto" in err_text or "apagado" in err_text or "restaur" in err_text

        # Invalid memory with preserved text -> restored to active
        r_ok = client.post("/v1/memory/mem_inv/restore", headers=_mut_headers("pat-u1", "k-t2"))
        assert r_ok.status_code == 200, r_ok.text
        assert r_ok.json()["status"] == "active"
        assert r_ok.json()["memory"]["fact"] == "Prefiro respostas diretas."

        # Cross-user restore -> 404
        r_cross = client.post("/v1/memory/mem_inv/restore", headers=_mut_headers("pat-u2", "k-t3"))
        assert r_cross.status_code == 404
    finally:
        engine.dispose()


def test_history_returns_chronological_events_and_never_exposes_forgotten_old_text(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "history.db")
    try:
        store = PostgresAgentMemoryStore(engine, user_id="user-1")
        saved = store.save(
            "Informacao sensivel esquecida.",
            kind="fact",
            source="user_explicit",
            context=MemoryWriteContext(source_kind="user", actor="user:user-1"),
        )
        mid = str(saved["memory_id"])
        assert store.forget(mid) is True

        # Even if a legacy/corrupted row had old_text on a forgotten event, the API MUST redact it.
        with engine.begin() as conn:
            conn.execute(
                update(memory_events)
                .where(memory_events.c.memory_id == mid, memory_events.c.event == "forgotten")
                .values(old_text="TEXTO_VAZADO_PROIBIDO")
            )

        client = TestClient(_make_app(engine))
        resp = client.get(f"/v1/memory/{mid}/history", headers=AUTH_U1)
        assert resp.status_code == 200, resp.text
        events = resp.json()["events"]
        assert [e["event"] for e in events] == ["created", "forgotten"]
        forgotten_ev = events[-1]
        assert forgotten_ev["old_text"] is None
        assert forgotten_ev["new_text"] is None
        assert "TEXTO_VAZADO_PROIBIDO" not in resp.text

        # Cross-user isolation on history
        cross = client.get(f"/v1/memory/{mid}/history", headers=AUTH_U2)
        assert cross.status_code == 404
    finally:
        engine.dispose()


def test_memory_review_routes_do_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    engine = _make_engine(tmp_path, "nonblocking.db")
    try:
        app = _make_app(engine)

        import agentos.api.memory_review_routes as mod

        orig = mod._list_review_payload

        def slow_list(*args, **kwargs):
            time.sleep(0.5)
            return orig(*args, **kwargs)

        monkeypatch.setattr(mod, "_list_review_payload", slow_list)

        async def _run() -> float:
            transport = ASGITransport(app=app)
            gc.collect()
            gc_was_enabled = gc.isenabled()
            gc.disable()
            try:
                started = time.monotonic()
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    slow = asyncio.ensure_future(client.get("/v1/memory/review", headers=AUTH_U1))
                    await asyncio.sleep(0.1)
                    fast_resp = await client.get("/v1/projects", headers=AUTH_U1)
                    assert fast_resp.status_code == 200
                    fast_elapsed = time.monotonic() - started
                    slow_resp = await slow
                    assert slow_resp.status_code == 200
                    return fast_elapsed
            finally:
                if gc_was_enabled:
                    gc.enable()

        elapsed = asyncio.run(_run())
        assert elapsed < 0.35, f"Event loop was stalled ({elapsed:.3f}s)"
    finally:
        engine.dispose()
