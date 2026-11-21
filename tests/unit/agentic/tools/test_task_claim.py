"""Unit tests for TaskClaim tool, feature flag gating, dependencies and Criterion 8 concurrency (WS-B3)."""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from agentos.agentic.agent_definitions import VERIFIER, AgentDefinition, AgentToolPolicy
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.task_board import TaskBoard
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.migrate import upgrade


class _Clock:
    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _sqlite_engine(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15.0},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    upgrade(engine)
    return engine


def test_task_board_disabled_hides_task_claim_and_preserves_in_memory_behavior(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path, "conv-off")
    ts1 = AgentToolset(ws, conversation_id="conv-off", todos_state=[])
    ts2 = AgentToolset(ws, conversation_id="conv-off", todos_state=[])

    # TaskClaim must NOT be published in definitions() or schemas() when disabled (default).
    def_names = {d.name for d in ts1.definitions()}
    schema_names = {s["function"]["name"] for s in ts1.schemas()}
    assert "task_claim" not in def_names
    assert "task_claim" not in schema_names
    assert "TaskClaim" not in schema_names

    # In-memory task_create / task_update / task_get / task_list and TodoWrite isolation work as in 0.12.31.
    created = ts1.execute("task_create", {"subject": "Tarefa local", "description": "Somente em memória"})
    assert created["ok"] is True
    tid = created["task"]["id"]
    assert tid == "todo_1"

    assert ts1.execute("task_get", {"task_id": tid})["task"]["subject"] == "Tarefa local"
    assert ts1.execute("task_update", {"task_id": tid, "status": "completed"})["task"]["status"] == "completed"
    assert len(ts1.execute("task_list", {})["tasks"]) == 1

    # Isolated toolset has its own todos_state and does not see ts1's tasks or TodoWrite.
    assert ts2.execute("task_list", {})["tasks"] == []
    ts2.execute("todo_write", {"todos": [{"content": "Item subagente", "status": "in_progress", "activeForm": "Fazendo"}]})
    assert len(ts2._todos) == 1
    assert len(ts1._todos) == 1 and ts1._todos[0]["id"] == "todo_1"


def test_task_board_enabled_publishes_task_claim_and_enforces_blocked_by(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 11, 21, 19, 0, 0, tzinfo=UTC))
    board = TaskBoard(conversation_id="conv-on", clock=clock)
    ws = ConversationWorkspace(tmp_path, "conv-on")
    ts = AgentToolset(
        ws,
        conversation_id="conv-on",
        task_board=board,
        task_board_enabled=True,
        task_board_agent_id="agent:sub-1",
    )

    def_names = {d.name for d in ts.definitions()}
    schema_names = {s["function"]["name"] for s in ts.schemas()}
    assert "task_claim" in def_names
    assert "task_claim" in schema_names

    # Create blocker and dependent task through TaskCreate / TaskUpdate.
    first = ts.execute("task_create", {"subject": "Tarefa A", "description": "Primeira"})
    assert first["ok"] is True
    id_a = first["task"]["id"]

    second = ts.execute("task_create", {"subject": "Tarefa B", "description": "Depende de A"})
    assert second["ok"] is True
    id_b = second["task"]["id"]

    upd = ts.execute("task_update", {"task_id": id_b, "add_blocked_by": [id_a]})
    assert upd["ok"] is True
    assert id_a in upd["task"]["blocked_by"]

    # Claiming B while A is pending fails clearly with TASK_BLOCKED.
    blocked_claim = ts.execute("task_claim", {"task_id": id_b})
    assert blocked_claim["ok"] is False
    assert blocked_claim["error_code"] == "TASK_BLOCKED"
    assert id_a in blocked_claim["content"]

    # Claim and complete A, then claim B succeeds.
    claim_a = ts.execute("task_claim", {"task_id": id_a})
    assert claim_a["ok"] is True
    assert claim_a["task"]["owner_agent_id"] == "agent:sub-1"
    assert claim_a["task"]["status"] == "in_progress"

    done_a = ts.execute("task_update", {"task_id": id_a, "status": "completed"})
    assert done_a["ok"] is True
    assert done_a["task"]["status"] == "completed"

    claim_b = ts.execute("task_claim", {"taskId": id_b})
    assert claim_b["ok"] is True
    assert claim_b["task"]["owner_agent_id"] == "agent:sub-1"

    # Lease expires after > 15 min, allowing another agent to claim B.
    clock.advance(901)
    ts_other = AgentToolset(
        ws,
        conversation_id="conv-on",
        task_board=board,
        task_board_enabled=True,
        task_board_agent_id="agent:sub-2",
    )
    reclaimed_b = ts_other.execute("task_claim", {"task_id": id_b})
    assert reclaimed_b["ok"] is True
    assert reclaimed_b["task"]["owner_agent_id"] == "agent:sub-2"


@pytest.mark.parametrize("backend", ["memory", "sql"])
def test_criterion_8_concurrent_task_claim_tool_barrier_exactly_one_wins(
    backend: str, tmp_path: Path
) -> None:
    """Critério 8 (Spec 6.5): dois subagentes chamando TaskClaim ao mesmo tempo -> exatamente um vence."""
    clock = _Clock(datetime(2026, 11, 21, 19, 15, 0, tzinfo=UTC))
    engine = _sqlite_engine(tmp_path / "criterion8.db") if backend == "sql" else None
    try:
        board = TaskBoard(conversation_id="conv-c8", engine=engine, clock=clock)
        item = board.create(title="Corrigir bug crítico", description="Sem duplicar trabalho")
        ws = ConversationWorkspace(tmp_path, "conv-c8")

        ts_alpha = AgentToolset(
            ws,
            conversation_id="conv-c8",
            task_board=board,
            task_board_enabled=True,
            task_board_agent_id="agent:alpha",
        )
        ts_beta = AgentToolset(
            ws,
            conversation_id="conv-c8",
            task_board=board,
            task_board_enabled=True,
            task_board_agent_id="agent:beta",
        )

        barrier = threading.Barrier(2, timeout=5)
        results: dict[str, dict] = {}
        errors: list[BaseException] = []
        lock = threading.Lock()

        def _attempt(name: str, toolset: AgentToolset) -> None:
            try:
                barrier.wait()
                res = toolset.execute("task_claim", {"task_id": item.item_id})
                with lock:
                    results[name] = res
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        t1 = threading.Thread(target=_attempt, args=("alpha", ts_alpha))
        t2 = threading.Thread(target=_attempt, args=("beta", ts_beta))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert errors == []
        assert set(results) == {"alpha", "beta"}

        winners = [name for name, res in results.items() if res["ok"] is True]
        losers = [name for name, res in results.items() if res["ok"] is False]
        assert len(winners) == 1
        assert len(losers) == 1

        winner_name = winners[0]
        loser_res = results[losers[0]]
        assert results[winner_name]["task"]["owner_agent_id"] == f"agent:{winner_name}"
        assert loser_res["error_code"] == "TASK_ALREADY_CLAIMED"
        assert "already claimed" in loser_res["content"].lower()
    finally:
        if engine is not None:
            engine.dispose()


def test_agent_tool_policy_allows_task_claim_only_for_task_capable_agents_when_enabled() -> None:
    worker_def = AgentDefinition(
        name="worker",
        description="Worker with task tools",
        prompt="Work on tasks.",
        origin="user",
        tools=("Read", "TaskList", "TaskUpdate"),
    )
    policy_off = AgentToolPolicy(None, worker_def, allow_task_board=False)
    policy_on = AgentToolPolicy(None, worker_def, allow_task_board=True)

    assert policy_off.allows("task_claim", ()) is False
    assert policy_on.allows("task_claim", ()) is True
    assert policy_on.allows("TaskClaim", ()) is True

    # Verifier has no task tools -> must not receive TaskClaim even when task board is enabled.
    verifier_policy = AgentToolPolicy(None, VERIFIER, allow_task_board=True)
    assert verifier_policy.allows("task_claim", ()) is False


def test_tool_activity_automatically_renews_owner_lease(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 11, 21, 19, 30, 0, tzinfo=UTC))
    board = TaskBoard(conversation_id="conv-renew", clock=clock)
    item = board.create(title="Tarefa longa")
    ws = ConversationWorkspace(tmp_path, "conv-renew")
    ts = AgentToolset(
        ws,
        conversation_id="conv-renew",
        task_board=board,
        task_board_enabled=True,
        task_board_agent_id="agent:worker-1",
    )

    claimed = ts.execute("task_claim", {"task_id": item.item_id})
    assert claimed["ok"] is True
    initial_exp = board.get(item.item_id).lease_expires_at
    assert initial_exp == clock.now + timedelta(seconds=900)

    # Advance 10 minutes and execute another tool (list_files) -> lease auto-renews!
    clock.advance(600)
    res = ts.execute("list_files", {})
    assert res["ok"] is True
    renewed_exp = board.get(item.item_id).lease_expires_at
    assert renewed_exp == clock.now + timedelta(seconds=900)
    assert renewed_exp > initial_exp

