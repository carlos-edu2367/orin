"""Unit tests for TaskBoard (WS-B3, B-RF3): SQL & in-memory parity, leases, dependencies and isolation."""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from agentos.agentic.task_board import (
    DEFAULT_LEASE_SECONDS,
    TaskBoard,
    normalize_board_status,
    to_tool_status,
)
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.task_board import TaskBoardItemRecord


class _MutableClock:
    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


def _sqlite_engine(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15.0},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    upgrade(engine)
    return engine


def _make_board(backend: str, tmp_path: Path, clock: _MutableClock, conversation_id: str = "conv-1"):
    if backend == "sql":
        engine = _sqlite_engine(tmp_path / "task_board.db")
        return TaskBoard(conversation_id=conversation_id, engine=engine, clock=clock), engine
    return TaskBoard(conversation_id=conversation_id, engine=None, clock=clock), None


@pytest.mark.parametrize("backend", ["memory", "sql"])
def test_task_board_create_blocked_by_claim_default_lease_renew_and_expire(
    backend: str, tmp_path: Path
) -> None:
    clock = _MutableClock(datetime(2026, 11, 21, 18, 0, 0, tzinfo=UTC))
    board, engine = _make_board(backend, tmp_path, clock)
    try:
        dep = board.create(title="Preparar esquema", description="Passo base")
        dependent = board.create(
            title="Implementar serviço",
            description="Depende do esquema",
            blocked_by=[dep.item_id],
        )
        assert isinstance(dep, TaskBoardItemRecord)
        assert dep.status == "pending"
        assert dependent.blocked_by == (dep.item_id,)

        # Blocked item cannot be claimed until dependency is done.
        assert board.claim(dependent.item_id, "agent-1") is None
        err_code, msg = board.explain_claim_failure(dependent.item_id, "agent-1")
        assert err_code == "TASK_BLOCKED"
        assert dep.item_id in msg

        # Claiming dep uses default 15-minute (900s) lease.
        claimed_dep = board.claim(dep.item_id, "agent-1")
        assert claimed_dep is not None
        assert claimed_dep.status == "in_progress"
        assert claimed_dep.owner_agent_id == "agent-1"
        assert claimed_dep.lease_expires_at == clock.now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
        assert DEFAULT_LEASE_SECONDS == 900

        # Renew owner leases after activity advances lease_expires_at.
        clock.advance(600)  # +10 min (still within 15 min lease)
        renewed_count = board.renew_owner_leases("agent-1")
        assert renewed_count == 1
        refreshed = board.get(dep.item_id)
        assert refreshed is not None
        assert refreshed.status == "in_progress"
        assert refreshed.owner_agent_id == "agent-1"
        assert refreshed.lease_expires_at == clock.now + timedelta(seconds=900)

        # Throttled rapid renewal within throttle window does not hit store again.
        assert board.renew_owner_leases("agent-1") == 0

        # Advancing past lease expiration (no renewal) returns item to pending automatically.
        clock.advance(901)
        expired = board.expire_leases()
        assert expired == 1
        after_expiry = board.get(dep.item_id)
        assert after_expiry is not None
        assert after_expiry.status == "pending"
        assert after_expiry.owner_agent_id is None
        assert after_expiry.lease_expires_at is None

        # Another agent can now claim and complete dep via status="completed" (-> "done").
        reclaimed = board.claim(dep.item_id, "agent-2")
        assert reclaimed is not None
        assert reclaimed.owner_agent_id == "agent-2"

        done_dep = board.update(dep.item_id, status="completed", acting_agent_id="agent-2")
        assert done_dep is not None
        assert done_dep.status == "done"
        assert done_dep.lease_expires_at is None

        # Now dependent is unblocked and can be claimed.
        claimed_dependent = board.claim(dependent.item_id, "agent-3")
        assert claimed_dependent is not None
        assert claimed_dependent.status == "in_progress"
        assert claimed_dependent.owner_agent_id == "agent-3"
    finally:
        if engine is not None:
            engine.dispose()


@pytest.mark.parametrize("backend", ["memory", "sql"])
def test_task_board_auto_expires_leases_before_list_get_and_claim(
    backend: str, tmp_path: Path
) -> None:
    clock = _MutableClock(datetime(2026, 11, 21, 18, 30, 0, tzinfo=UTC))
    board, engine = _make_board(backend, tmp_path, clock)
    try:
        item = board.create(title="Tarefa com prazo")
        claimed = board.claim(item.item_id, "agent-a")
        assert claimed is not None
        assert claimed.status == "in_progress"

        # Advance 15 min + 1 sec without explicitly calling expire_leases().
        clock.advance(901)

        # list_items() auto-expires and shows pending with no owner.
        listed = board.list_items()
        assert len(listed) == 1
        assert listed[0].status == "pending"
        assert listed[0].owner_agent_id is None

        # Re-claim, expire again, and verify direct claim() auto-expires and succeeds for agent-b.
        assert board.claim(item.item_id, "agent-a") is not None
        clock.advance(901)
        reclaimed = board.claim(item.item_id, "agent-b")
        assert reclaimed is not None
        assert reclaimed.owner_agent_id == "agent-b"
        assert reclaimed.status == "in_progress"
    finally:
        if engine is not None:
            engine.dispose()


@pytest.mark.parametrize("backend", ["memory", "sql"])
def test_task_board_atomic_concurrent_claim(backend: str, tmp_path: Path) -> None:
    clock = _MutableClock(datetime(2026, 11, 21, 19, 0, 0, tzinfo=UTC))
    board, engine = _make_board(backend, tmp_path, clock)
    try:
        item = board.create(title="Recurso único")
        barrier = threading.Barrier(4, timeout=5)
        winners: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def _worker(agent_id: str) -> None:
            try:
                barrier.wait()
                claimed = board.claim(item.item_id, agent_id)
                if claimed is not None:
                    with lock:
                        winners.append(agent_id)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(f"agent-{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert len(winners) == 1
        stored = board.get(item.item_id)
        assert stored is not None
        assert stored.owner_agent_id == winners[0]
        assert stored.status == "in_progress"
    finally:
        if engine is not None:
            engine.dispose()


def test_status_mapping_between_tool_and_board() -> None:
    assert normalize_board_status("completed") == "done"
    assert normalize_board_status("done") == "done"
    assert normalize_board_status("deleted") == "cancelled"
    assert normalize_board_status("pending") == "pending"
    assert normalize_board_status("in_progress") == "in_progress"
    assert normalize_board_status("blocked") == "blocked"
    assert to_tool_status("done") == "completed"
    assert to_tool_status("cancelled") == "deleted"
    assert to_tool_status("pending") == "pending"
    assert to_tool_status("in_progress") == "in_progress"
