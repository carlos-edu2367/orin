from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from sqlalchemy import create_engine, select

from agentos.agentic import transcript as turn_transcript
from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    conversation_activity_events,
    conversation_tool_records,
    conversation_turn_steps,
    metadata,
)
from agentos.persistence.sqlite import create_local_engine


def test_pending_returns_one_turn_per_idle_conversation_and_skips_active_conversations() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    t1_a = store.create(
        user_id="user-1",
        message="conv 1 first",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="c1-t1",
    )
    t1_b = store.create(
        user_id="user-1",
        message="conv 1 second",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="c1-t2",
        conversation_id=t1_a.conversation_id,
    )
    t2_a = store.create(
        user_id="user-1",
        message="conv 2 first",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="c2-t1",
    )

    # Before any claim: pending() must only return the earliest turn per idle conversation
    pending_before = store.pending()
    assert pending_before == (t1_a.turn_id, t2_a.turn_id)
    assert t1_b.turn_id not in pending_before

    # Claim conv 1's first turn
    claimed_t1_a = store.claim(t1_a.turn_id)
    assert claimed_t1_a is not None
    assert claimed_t1_a["turn_id"] == t1_a.turn_id

    # While t1_a is running, conv 1 is active:
    # 1) pending() must skip t1_b and only return t2_a
    assert store.pending() == (t2_a.turn_id,)
    # 2) Direct claim(t1_b) must be rejected (return None)
    assert store.claim(t1_b.turn_id) is None

    # Meanwhile, conv 2 is independent and can be claimed concurrently
    claimed_t2_a = store.claim(t2_a.turn_id)
    assert claimed_t2_a is not None
    assert claimed_t2_a["turn_id"] == t2_a.turn_id

    # Both conversations are now active, so pending() is empty
    assert store.pending() == ()

    # Finish conv 1's first turn: conv 1 still has t1_b queued, so t1_b becomes eligible
    store.finish(claimed_t1_a)
    assert store.get(t1_a.conversation_id, "user-1")["state"] == "queued"
    assert store.pending() == (t1_b.turn_id,)

    claimed_t1_b = store.claim(t1_b.turn_id)
    assert claimed_t1_b is not None
    assert claimed_t1_b["turn_id"] == t1_b.turn_id

    store.finish(claimed_t1_b)
    store.finish(claimed_t2_a)
    assert store.get(t1_a.conversation_id, "user-1")["state"] == "completed"
    assert store.get(t2_a.conversation_id, "user-1")["state"] == "completed"


def test_concurrent_claim_race_allows_only_one_turn_for_same_conversation(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{tmp_path / 'race_claim.db'}")
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    t1_a = store.create(
        user_id="user-1",
        message="msg 1",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="race-1",
    )
    t1_b = store.create(
        user_id="user-1",
        message="msg 2",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="race-2",
        conversation_id=t1_a.conversation_id,
    )

    barrier = threading.Barrier(2)

    def try_claim(turn_id: str) -> dict[str, object] | None:
        barrier.wait(timeout=5)
        return store.claim(turn_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(try_claim, t1_a.turn_id)
        f2 = pool.submit(try_claim, t1_b.turn_id)
        results = [f1.result(), f2.result()]

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0]["turn_id"] in {t1_a.turn_id, t1_b.turn_id}
    engine.dispose()


def test_concurrent_activity_tool_call_and_step_recording_has_no_sequence_collision(tmp_path: Path) -> None:
    from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore

    engine = create_local_engine(f"sqlite:///{tmp_path / 'concurrent_seq.db'}")
    metadata.create_all(engine)
    store = PostgresChatStore(
        engine,
        activity_store=PostgresAgenticActivityStore(engine, cursor_secret="test-secret"),
    )

    receipt = store.create(
        user_id="user-1",
        message="parallel subagents turn",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="seq-turn-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    workers = 20
    b_act = threading.Barrier(workers)
    b_tool = threading.Barrier(workers)
    b_step = threading.Barrier(workers)

    def emit_all(idx: int) -> None:
        b_act.wait(timeout=5)
        store.record(
            turn,
            AgentActivityEventType.TOOL_STARTED,
            f"Tool {idx} started",
            {"idx": idx},
            agent_id=f"agent:sub:{idx}",
        )
        b_tool.wait(timeout=5)
        store.record_tool_call(
            turn,
            tool_name="read_file",
            arguments={"path": f"file_{idx}.py"},
            status="succeeded",
            summary=f"read file_{idx}.py",
        )
        b_step.wait(timeout=5)
        store.record_step(
            turn,
            kind=turn_transcript.STEP_TOOL_RESULT,
            payload={"call_id": f"call_{idx}", "output": "ok"},
            agent_id="main",
            tool_name="read_file",
            tool_call_id=f"call_{idx}",
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(emit_all, i) for i in range(workers)]
        for f in futures:
            f.result()

    with engine.connect() as conn:
        act_seqs = conn.execute(
            select(conversation_activity_events.c.sequence)
            .where(conversation_activity_events.c.turn_id == receipt.turn_id)
            .order_by(conversation_activity_events.c.sequence)
        ).scalars().all()
        tool_seqs = conn.execute(
            select(conversation_tool_records.c.sequence)
            .where(conversation_tool_records.c.turn_id == receipt.turn_id)
            .order_by(conversation_tool_records.c.sequence)
        ).scalars().all()
        step_seqs = conn.execute(
            select(conversation_turn_steps.c.sequence)
            .where(
                conversation_turn_steps.c.turn_id == receipt.turn_id,
                conversation_turn_steps.c.agent_id == "main",
            )
            .order_by(conversation_turn_steps.c.sequence)
        ).scalars().all()

    assert len(act_seqs) == len(set(act_seqs))
    assert len(act_seqs) == workers + 2  # turn.started + claim activity + 20 tool activities
    assert list(tool_seqs) == list(range(1, workers + 1))
    assert list(step_seqs) == list(range(1, workers + 1))
    engine.dispose()


def test_create_local_engine_supports_at_least_20_concurrent_checked_out_connections(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{tmp_path / 'pool_cap.db'}")
    metadata.create_all(engine)
    workers = 20
    barrier = threading.Barrier(workers)

    def hold_conn() -> int:
        with engine.connect() as conn:
            barrier.wait(timeout=2)
            return int(conn.exec_driver_sql("SELECT 1").scalar() or 0)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: hold_conn(), range(workers)))

    assert results == [1] * workers
    engine.dispose()


def test_concurrent_turn_runner_executes_two_sessions_simultaneously(tmp_path: Path) -> None:
    from agentos.agentic.runtime import AgenticRunResult
    from agentos.workers.chat import ChatWorker
    from agentos.workers.publisher import ConcurrentTurnRunner, poll_once

    engine = create_local_engine(f"sqlite:///{tmp_path / 'runner_parallel.db'}")
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    t1_a = store.create(
        user_id="user-1",
        message="session 1 turn 1",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="s1-t1",
    )
    t1_b = store.create(
        user_id="user-1",
        message="session 1 turn 2",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="s1-t2",
        conversation_id=t1_a.conversation_id,
    )
    t2_a = store.create(
        user_id="user-1",
        message="session 2 turn 1",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="s2-t1",
    )

    barrier = threading.Barrier(2)
    execution_order: list[str] = []
    order_lock = threading.Lock()

    class BarrierRuntime:
        def __init__(self, turn: dict[str, object]) -> None:
            self._turn = turn

        def run(self, turn_id: str) -> AgenticRunResult:
            if turn_id in (t1_a.turn_id, t2_a.turn_id):
                # Both session 1 and session 2 must be inside run() simultaneously
                barrier.wait(timeout=3.0)
            with order_lock:
                execution_order.append(turn_id)
            store.delta(self._turn, f"reply for {turn_id}")
            store.finish(self._turn)
            return AgenticRunResult("completed")

        def close(self) -> None:
            return None

    worker = ChatWorker(store, runtime_factory=lambda turn: BarrierRuntime(turn))
    worker._project = lambda *args, **kwargs: None

    runner = ConcurrentTurnRunner(worker, max_workers=4)
    try:
        ran, _ = poll_once(runner, 0.0)
        assert ran is True
        runner.wait_all(timeout=5.0)

        # Both t1_a and t2_a ran concurrently; t1_b was held back because conv 1 was active
        assert set(execution_order) == {t1_a.turn_id, t2_a.turn_id}
        assert store.get(t2_a.conversation_id, "user-1")["state"] == "completed"
        assert store.get(t1_a.conversation_id, "user-1")["state"] == "queued"

        # Next poll picks up t1_b now that t1_a is finished
        ran_second, _ = poll_once(runner, 0.0)
        assert ran_second is True
        runner.wait_all(timeout=5.0)
        assert execution_order[-1] == t1_b.turn_id
        assert store.get(t1_a.conversation_id, "user-1")["state"] == "completed"
    finally:
        runner.shutdown(wait=True)
        engine.dispose()


def test_watchdog_does_not_fail_turn_queued_behind_running_turn_or_busy_worker() -> None:
    from datetime import UTC, datetime, timedelta
    from sqlalchemy import update
    from agentos.persistence.postgres.schema import conversation_dispatches
    from agentos.workers.chat import ChatWorker

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    t1_a = store.create(
        user_id="user-1",
        message="conv 1 running",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="wd-c1-t1",
    )
    t1_b = store.create(
        user_id="user-1",
        message="conv 1 queued behind running",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="wd-c1-t2",
        conversation_id=t1_a.conversation_id,
    )
    t2_a = store.create(
        user_id="user-1",
        message="conv 2 queued while worker busy",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="wd-c2-t1",
    )

    claimed_t1_a = store.claim(t1_a.turn_id)
    assert claimed_t1_a is not None
    store.heartbeat("chat-worker")

    old_queued_at = datetime.now(UTC) - timedelta(seconds=120)
    with engine.begin() as conn:
        conn.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id.in_((t1_b.turn_id, t2_a.turn_id)))
            .values(queued_at=old_queued_at)
        )

    worker = ChatWorker(store)
    # While t1_a is running and worker heartbeat is fresh, neither t1_b nor t2_a may be expired
    assert worker.watchdog(maximum_age=timedelta(seconds=30)) == ()

    # Once t1_a finishes and no turns are running, an unacquired old turn IS expired
    store.finish(claimed_t1_a)
    expired = worker.watchdog(maximum_age=timedelta(seconds=30))
    assert set(expired) == {t1_b.turn_id, t2_a.turn_id}


def test_chat_worker_isolates_hook_engine_per_turn(monkeypatch) -> None:
    from agentos.workers import chat as chat_module
    from agentos.workers.chat import ChatWorker

    class Settings:
        def get(self, user_id: str) -> dict[str, object]:
            return {"max_iterations": 8}

    class SkillLibrary:
        def registry_for(self, user_id: str, *, agent_id: str) -> None:
            return None

    captured_hook_engines: list[object] = []

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            captured_hook_engines.append(kwargs.get("hook_engine"))

        def build_runtime(self) -> object:
            return object()

    class StubStore:
        _engine = create_engine("sqlite://", future=True)

        def main_agent_id(self, turn: object) -> str:
            return "agent-main"

    monkeypatch.setattr(chat_module, "PostgresSkillLibraryService", lambda engine: SkillLibrary())
    monkeypatch.setattr(chat_module, "ConversationAgentStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(chat_module, "PostgresAgentMemoryStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "search_client_from_environment", lambda: None)
    monkeypatch.setattr(chat_module, "TurnSession", FakeSession)

    worker = ChatWorker(StubStore(), runtime_settings=Settings())
    turn_1 = {"turn_id": "t1", "conversation_id": "c1", "user_id": "u1", "execution_id": "e1", "assistant_message_id": "m1"}
    turn_2 = {"turn_id": "t2", "conversation_id": "c2", "user_id": "u1", "execution_id": "e2", "assistant_message_id": "m2"}

    worker._runtime_for(turn_1)
    worker._runtime_for(turn_2)

    assert len(captured_hook_engines) == 2
    assert captured_hook_engines[0] is not None
    assert captured_hook_engines[1] is not None
    assert captured_hook_engines[0] is not captured_hook_engines[1]


