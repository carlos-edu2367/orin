"""Tests for TaskBoardStore atomic claim, dependencies, leases and isolation (WS-B0, Spec 6.3)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.task_board import TaskBoardItemRecord, TaskBoardStore


def _memory_engine():
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    return engine


def _file_sqlite_engine(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15.0},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    upgrade(engine)
    return engine


def test_task_board_crud_claim_renew_complete_and_conversation_isolation() -> None:
    engine = _memory_engine()
    t0 = datetime(2026, 11, 21, 17, 0, 0, tzinfo=UTC)
    store = TaskBoardStore(engine, clock=lambda: t0)

    item = store.create(
        conversation_id="conv-1",
        team_name="backend",
        title="Implementar store",
        description="Criar TaskBoardStore com claim atômico.",
    )
    assert isinstance(item, TaskBoardItemRecord)
    assert item.conversation_id == "conv-1"
    assert item.team_name == "backend"
    assert item.status == "pending"
    assert item.owner_agent_id is None
    assert item.blocked_by == ()
    assert item.lease_expires_at is None
    assert item.created_at == t0

    # Other conversation cannot see, update or claim it.
    assert store.get(item.item_id, conversation_id="conv-2") is None
    assert store.list_for_conversation("conv-2") == []
    assert store.update(item.item_id, conversation_id="conv-2", title="Hack") is None
    assert store.claim(item.item_id, "agent-intruder", conversation_id="conv-2") is None

    # Update within same conversation works.
    updated = store.update(item.item_id, conversation_id="conv-1", description="Descrição revisada.")
    assert updated is not None
    assert updated.description == "Descrição revisada."

    # Claim succeeds once; second claim fails.
    claimed = store.claim(item.item_id, "agent-a", conversation_id="conv-1", lease_seconds=600, now=t0)
    assert claimed is not None
    assert claimed.status == "in_progress"
    assert claimed.owner_agent_id == "agent-a"
    assert claimed.lease_expires_at == t0 + timedelta(seconds=600)

    assert store.claim(item.item_id, "agent-b", conversation_id="conv-1", lease_seconds=600, now=t0) is None

    # Renew only works for the owning agent in the same conversation.
    t1 = t0 + timedelta(seconds=300)
    assert store.renew(item.item_id, "agent-b", conversation_id="conv-1", lease_seconds=600, now=t1) is None
    assert store.renew(item.item_id, "agent-a", conversation_id="conv-2", lease_seconds=600, now=t1) is None
    renewed = store.renew(item.item_id, "agent-a", conversation_id="conv-1", lease_seconds=900, now=t1)
    assert renewed is not None
    assert renewed.lease_expires_at == t1 + timedelta(seconds=900)

    # Complete only works for owning agent in same conversation.
    t2 = t1 + timedelta(seconds=60)
    assert store.complete(item.item_id, "agent-b", conversation_id="conv-1", now=t2) is None
    done = store.complete(
        item.item_id,
        "agent-a",
        conversation_id="conv-1",
        result_ref=".orin/agent-outputs/item-1.md",
        now=t2,
    )
    assert done is not None
    assert done.status == "done"
    assert done.owner_agent_id == "agent-a"
    assert done.lease_expires_at is None
    assert done.result_ref == ".orin/agent-outputs/item-1.md"


def test_task_board_blocks_claim_until_all_dependencies_in_same_conversation_are_done() -> None:
    engine = _memory_engine()
    t0 = datetime(2026, 11, 21, 17, 10, 0, tzinfo=UTC)
    store = TaskBoardStore(engine, clock=lambda: t0)

    dep1 = store.create(conversation_id="conv-1", item_id="t-1", title="Base 1")
    dep2 = store.create(conversation_id="conv-1", item_id="t-2", title="Base 2")
    child = store.create(
        conversation_id="conv-1",
        item_id="t-3",
        title="Dependente",
        blocked_by=[dep1.item_id, dep2.item_id],
    )
    missing_dep_item = store.create(
        conversation_id="conv-1",
        item_id="t-missing-dep",
        title="Depende de item inexistente",
        blocked_by=["nonexistent-dep"],
    )
    # A done item with the same id in ANOTHER conversation must NOT unblock this conversation.
    other_done = store.create(conversation_id="conv-other", item_id="foreign-done", title="Done lá fora")
    store.claim(other_done.item_id, "ag-x", conversation_id="conv-other", now=t0)
    store.complete(other_done.item_id, "ag-x", conversation_id="conv-other", now=t0)
    cross_conv_child = store.create(
        conversation_id="conv-1",
        item_id="t-cross-dep",
        title="Depende de item de outra conversa",
        blocked_by=["foreign-done"],
    )

    # Cannot claim while dep1 & dep2 are pending.
    assert store.claim(child.item_id, "ag-1", conversation_id="conv-1", now=t0) is None
    assert store.claim(missing_dep_item.item_id, "ag-1", conversation_id="conv-1", now=t0) is None
    assert store.claim(cross_conv_child.item_id, "ag-1", conversation_id="conv-1", now=t0) is None

    # Complete dep1 only -> still blocked by dep2.
    assert store.claim(dep1.item_id, "ag-1", conversation_id="conv-1", now=t0) is not None
    assert store.complete(dep1.item_id, "ag-1", conversation_id="conv-1", now=t0) is not None
    assert store.claim(child.item_id, "ag-1", conversation_id="conv-1", now=t0) is None

    # Complete dep2 -> child becomes claimable!
    assert store.claim(dep2.item_id, "ag-1", conversation_id="conv-1", now=t0) is not None
    assert store.complete(dep2.item_id, "ag-1", conversation_id="conv-1", now=t0) is not None
    claimed_child = store.claim(child.item_id, "ag-2", conversation_id="conv-1", now=t0)
    assert claimed_child is not None
    assert claimed_child.status == "in_progress"
    assert claimed_child.owner_agent_id == "ag-2"


def test_task_board_expire_leases_returns_expired_in_progress_items_to_pending() -> None:
    engine = _memory_engine()
    t0 = datetime(2026, 11, 21, 17, 20, 0, tzinfo=UTC)
    store = TaskBoardStore(engine, clock=lambda: t0)

    expiring = store.create(conversation_id="conv-1", item_id="t-exp", title="Vai expirar")
    fresh = store.create(conversation_id="conv-1", item_id="t-fresh", title="Ainda válida")
    store.claim(expiring.item_id, "ag-1", conversation_id="conv-1", lease_seconds=60, now=t0)
    store.claim(fresh.item_id, "ag-2", conversation_id="conv-1", lease_seconds=600, now=t0)

    expired_count = store.expire_leases(now=t0 + timedelta(seconds=60))
    assert expired_count == 1

    reloaded_exp = store.get("t-exp", conversation_id="conv-1")
    assert reloaded_exp is not None
    assert reloaded_exp.status == "pending"
    assert reloaded_exp.owner_agent_id is None
    assert reloaded_exp.lease_expires_at is None

    reloaded_fresh = store.get("t-fresh", conversation_id="conv-1")
    assert reloaded_fresh is not None
    assert reloaded_fresh.status == "in_progress"
    assert reloaded_fresh.owner_agent_id == "ag-2"

    # Expired task can now be claimed by another agent.
    reclaimed = store.claim("t-exp", "ag-3", conversation_id="conv-1", now=t0 + timedelta(seconds=61))
    assert reclaimed is not None
    assert reclaimed.owner_agent_id == "ag-3"


def test_task_board_concurrent_claim_with_barrier_allows_exactly_one_winner(tmp_path: Path) -> None:
    """Acceptance criterion 8: 2 threads racing on SQLite with Barrier(2, timeout=5)."""
    engine = _file_sqlite_engine(tmp_path / "task_board_race.db")
    try:
        store = TaskBoardStore(engine)
        item = store.create(conversation_id="conv-race", item_id="t-race", title="Tarefa disputada")

        barrier = threading.Barrier(2, timeout=5)
        results: dict[str, TaskBoardItemRecord | None] = {}
        errors: list[BaseException] = []

        def worker(agent_id: str) -> None:
            try:
                barrier.wait()
                results[agent_id] = store.claim(
                    item.item_id,
                    agent_id,
                    conversation_id="conv-race",
                    lease_seconds=900,
                )
            except BaseException as exc:  # pragma: no cover - surfaced in assertion below
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("agent-1",))
        t2 = threading.Thread(target=worker, args=("agent-2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected thread errors during concurrent claim: {errors!r}"
        winners = [aid for aid, rec in results.items() if rec is not None]
        losers = [aid for aid, rec in results.items() if rec is None]
        assert len(winners) == 1
        assert len(losers) == 1

        winner = winners[0]
        persisted = store.get(item.item_id, conversation_id="conv-race")
        assert persisted is not None
        assert persisted.status == "in_progress"
        assert persisted.owner_agent_id == winner
    finally:
        engine.dispose()
