"""WS-C7: a pipeline run that lost its turn is interrupted at boot; the worker never pauses a pipeline turn (S2, R6)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from agentos.persistence.postgres.schema import conversation_turns, metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.recovery import interrupt_orphaned_pipeline_runs
from agentos.pipelines.store import PipelineStore


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(eng)
    yield eng
    eng.dispose()


def _turn(engine, turn_id: str, state: str) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(conversation_turns).values(
                turn_id=turn_id, conversation_id="c1", user_id="u1", execution_id=f"exe-{turn_id}",
                user_message_id=f"um-{turn_id}", assistant_message_id=f"am-{turn_id}", provider="anthropic",
                model_id="m", state=state, idempotency_key=f"idem-{turn_id}", created_at=now, updated_at=now,
                turn_kind="pipeline",
            )
        )


def _run(store: PipelineStore, tmp_path: Path, run_id: str, turn_id: str | None, **extra):
    store.create_run(
        run_id=run_id, user_id="u1", conversation_id="c1", workspace_root=tmp_path.as_posix(), pipeline_id="p",
        definition_hash="h" * 64, source="user", inputs={}, turn_id=turn_id, **extra,
    )
    store.start_step(run_id=run_id, step_id="a", input_hash="1" * 64)


def test_a_run_whose_turn_ended_becomes_interrupted_with_its_open_steps(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    for turn_id, state in (("t-done", "completed"), ("t-failed", "failed"), ("t-cancelled", "cancelled")):
        _turn(engine, turn_id, state)
        _run(store, tmp_path, f"run-{turn_id}", turn_id)
    assert interrupt_orphaned_pipeline_runs(engine) == 3
    for turn_id in ("t-done", "t-failed", "t-cancelled"):
        assert store.get_run(f"run-{turn_id}").state == "interrupted"
        assert [row.state for row in store.steps_of(f"run-{turn_id}")] == ["interrupted"]


def test_a_run_whose_turn_is_still_alive_is_left_alone(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    for turn_id, state in (("t-run", "running"), ("t-queued", "queued"), ("t-start", "starting"), ("t-paused", "paused")):
        _turn(engine, turn_id, state)
        _run(store, tmp_path, f"run-{turn_id}", turn_id)
    assert interrupt_orphaned_pipeline_runs(engine) == 0
    assert {store.get_run(f"run-{t}").state for t in ("t-run", "t-queued", "t-start", "t-paused")} == {"running"}


def test_a_run_pointing_at_a_vanished_turn_is_an_orphan(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    _run(store, tmp_path, "run-ghost", "no-such-turn")
    assert interrupt_orphaned_pipeline_runs(engine) == 1
    assert store.get_run("run-ghost").state == "interrupted"


def test_a_run_without_a_turn_is_only_an_orphan_once_it_is_old_enough(engine, tmp_path: Path) -> None:
    """A run that was just created is waiting for its turn to be claimed; a stale one never got a turn."""
    store = PipelineStore(engine)
    _run(store, tmp_path, "run-fresh", None)
    _run(store, tmp_path, "run-old", None, started_at=datetime.now(UTC) - timedelta(hours=2))
    assert interrupt_orphaned_pipeline_runs(engine) == 1
    assert store.get_run("run-fresh").state == "running"
    assert store.get_run("run-old").state == "interrupted"


def test_finished_runs_are_never_touched(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    _turn(engine, "t1", "completed")
    _run(store, tmp_path, "run-ok", "t1")
    store.set_run_state("run-ok", "succeeded")
    assert interrupt_orphaned_pipeline_runs(engine) == 0
    assert store.get_run("run-ok").state == "succeeded"


def test_the_sweep_races_safely_with_a_resumed_run(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    _turn(engine, "t1", "failed")
    _run(store, tmp_path, "run-1", "t1")
    results: list[int] = []
    barrier = threading.Barrier(4, timeout=5)

    def sweep() -> None:
        barrier.wait()
        results.append(interrupt_orphaned_pipeline_runs(engine))

    threads = [threading.Thread(target=sweep) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sum(results) >= 1 and store.get_run("run-1").state == "interrupted"


# ----------------------------------------------------------------- boot sweep + S2 through the real worker
def _chat_store(tmp_path: Path):
    from agentos.conversations.chat import PostgresChatStore

    eng = create_local_engine(sqlite_url(tmp_path / "chat.db"))
    metadata.create_all(eng)
    return eng, PostgresChatStore(eng)


def test_recover_once_interrupts_orphaned_pipeline_runs_at_boot(tmp_path: Path) -> None:
    from agentos.workers import publisher

    eng, chat = _chat_store(tmp_path)
    previous = publisher._ORPHANS_SWEPT
    publisher._ORPHANS_SWEPT = False
    try:
        _turn(eng, "t-dead", "failed")
        _run(PipelineStore(eng), tmp_path, "run-dead", "t-dead")
        publisher.recover_once(chat, has_in_flight=False)
        assert PipelineStore(eng).get_run("run-dead").state == "interrupted"
    finally:
        publisher._ORPHANS_SWEPT = previous
        eng.dispose()


def _claimed_turn(chat, kind: str | None, key: str):
    receipt = chat.create(
        user_id="u1", message="/pipeline run-1" if kind == "pipeline" else "faça algo", provider="openrouter", model_id="m1",
        idempotency_key=key, turn_kind=kind,
    )
    turn = chat.claim(str(receipt.turn_id))
    assert turn is not None
    return receipt, turn


@pytest.mark.parametrize("kind", ["pipeline", None])
def test_a_pipeline_turn_with_unresolved_effects_is_requeued_not_paused_but_an_ordinary_turn_still_pauses(tmp_path: Path, kind) -> None:
    """S2 / R6: the gate command is an effect of the turn; the pipeline journal reconciles it, the worker must not."""
    from agentos.workers.chat import ChatWorker

    eng, chat = _chat_store(tmp_path)
    try:
        receipt, turn = _claimed_turn(chat, kind, f"k-{kind}")
        worker = ChatWorker(chat)
        worker._project(turn, "STARTING", "worker_acquired")
        worker._project(turn, "RUNNING", "provider_started")
        worker._effect_started(turn, kind="tool", invocation_ref="tool:pipeline_gate_1", request_ref="tool:run_command")
        recovered = worker.recover_stale(maximum_age=timedelta(seconds=-5))
        state = chat.get(str(receipt.conversation_id), "u1")["state"]
        if kind == "pipeline":
            assert str(receipt.turn_id) in recovered and state == "queued"
        else:  # the regression guard: every other turn keeps pausing for reconciliation
            assert str(receipt.turn_id) not in recovered and state == "paused"
    finally:
        eng.dispose()


def test_a_requeued_pipeline_turn_runs_again_and_resumes_the_same_run(tmp_path: Path) -> None:
    from agentos.workers.chat import ChatWorker

    eng, chat = _chat_store(tmp_path)
    try:
        receipt, turn = _claimed_turn(chat, "pipeline", "k-again")
        worker = ChatWorker(chat)
        worker._project(turn, "STARTING", "worker_acquired")
        worker._project(turn, "RUNNING", "provider_started")
        worker.recover_stale(maximum_age=timedelta(seconds=-5))
        again = chat.claim(str(receipt.turn_id))
        assert again is not None and again["turn_kind"] == "pipeline"  # the same turn comes back to be claimed
    finally:
        eng.dispose()
