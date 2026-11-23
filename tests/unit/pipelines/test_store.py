"""PipelineStore: durable journal of pipeline runs and steps (WS-C3)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert

from agentos.persistence.postgres.schema import agent_runs, metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.store import (
    ARTIFACT_THRESHOLD_BYTES,
    PipelineStore,
    PipelineStoreError,
)


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture()
def store(engine) -> PipelineStore:
    return PipelineStore(engine)


def _run(store: PipelineStore, workspace: Path, run_id: str = "run-1", **overrides):
    values = dict(
        run_id=run_id,
        user_id="u1",
        conversation_id="c1",
        workspace_root=workspace.as_posix(),
        pipeline_id="implementar-verificar",
        definition_hash="h" * 64,
        source="builtin",
        inputs={"task": "x"},
        definition_snapshot="schema_version: 1\n",
    )
    values.update(overrides)
    return store.create_run(**values)


def test_create_run_persists_every_column(store: PipelineStore, workspace: Path) -> None:
    run = _run(store, workspace, turn_id="turn-1")
    assert run.state == "running"
    loaded = store.get_run("run-1")
    assert loaded is not None
    assert loaded.pipeline_id == "implementar-verificar"
    assert loaded.inputs == {"task": "x"}
    assert loaded.definition_snapshot == "schema_version: 1\n"
    assert loaded.turn_id == "turn-1"
    assert loaded.stop_reason is None
    assert loaded.used_tokens == 0
    assert loaded.used_cost_usd is None
    assert loaded.finished_at is None
    assert loaded.started_at.tzinfo is not None
    assert store.get_run("missing") is None


def test_create_run_rejects_duplicates_and_unknown_values(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    with pytest.raises(PipelineStoreError):
        _run(store, workspace)
    with pytest.raises(PipelineStoreError):
        _run(store, workspace, run_id="run-2", source="alien")
    with pytest.raises(PipelineStoreError):
        _run(store, workspace, run_id="run-3", state="exploded")


def test_list_runs_orders_newest_first(store: PipelineStore, workspace: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(3):
        _run(store, workspace, run_id=f"run-{index}", started_at=base + timedelta(minutes=index))
    _run(store, workspace, run_id="other", conversation_id="c2")
    assert [run.run_id for run in store.list_runs("c1")] == ["run-2", "run-1", "run-0"]
    assert [run.run_id for run in store.list_runs("c2")] == ["other"]


def test_step_is_written_as_running_with_hash_before_it_starts(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    step = store.start_step(run_id="run-1", step_id="implementar", input_hash="a" * 64)
    assert step.state == "running"
    assert step.attempt == 1
    assert step.item_key == ""
    assert step.input_hash == "a" * 64
    assert step.started_at is not None and step.finished_at is None
    assert store.steps_of("run-1")[0].state == "running"


def test_attempt_is_monotonic_per_step_and_item(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    first = store.start_step(run_id="run-1", step_id="s", item_key="a", input_hash="1" * 64)
    second = store.start_step(run_id="run-1", step_id="s", item_key="a", input_hash="1" * 64)
    other_item = store.start_step(run_id="run-1", step_id="s", item_key="b", input_hash="2" * 64)
    other_step = store.start_step(run_id="run-1", step_id="t", item_key="a", input_hash="3" * 64)
    assert (first.attempt, second.attempt, other_item.attempt, other_step.attempt) == (1, 2, 1, 1)


def test_attempt_stays_unique_with_eight_concurrent_writers(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    barrier = threading.Barrier(8, timeout=10)
    attempts: list[int] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait()
            step = store.start_step(run_id="run-1", step_id="verificar", item_key="", input_hash="f" * 64, loop_count=1)
            with guard:
                attempts.append(step.attempt)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert sorted(attempts) == list(range(1, 9))


def test_attempt_is_unique_across_two_store_instances(engine, workspace: Path) -> None:
    first, second = PipelineStore(engine), PipelineStore(engine)
    _run(first, workspace)
    results: list[int] = []
    barrier = threading.Barrier(6, timeout=10)

    def worker(target: PipelineStore) -> None:
        barrier.wait()
        results.append(target.start_step(run_id="run-1", step_id="s", input_hash="1" * 64).attempt)

    threads = [threading.Thread(target=worker, args=(first if i % 2 else second,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == [1, 2, 3, 4, 5, 6]


def test_find_reusable_returns_the_latest_row_of_the_loop_iteration(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    a1 = store.start_step(run_id="run-1", step_id="v", input_hash="1" * 64, loop_count=0)
    store.finish_step("run-1", "v", "", a1.attempt, state="failed", error="falhou")
    a2 = store.start_step(run_id="run-1", step_id="v", input_hash="1" * 64, loop_count=0)
    store.finish_step("run-1", "v", "", a2.attempt, state="succeeded", output={"ok": True})
    loop1 = store.start_step(run_id="run-1", step_id="v", input_hash="2" * 64, loop_count=1)
    store.finish_step("run-1", "v", "", loop1.attempt, state="succeeded", output={"ok": False})

    zero = store.find_reusable("run-1", "v", "", 0)
    one = store.find_reusable("run-1", "v", "", 1)
    assert zero is not None and zero.attempt == 2 and zero.state == "succeeded" and zero.output == {"ok": True}
    assert one is not None and one.input_hash == "2" * 64
    assert store.find_reusable("run-1", "v", "", 2) is None
    assert store.find_reusable("run-1", "nope", "", 0) is None


def test_finish_step_records_output_error_usage_and_truncates_error(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    step = store.start_step(run_id="run-1", step_id="s", input_hash="1" * 64, agent_id="agent-9")
    done = store.finish_step(
        "run-1", "s", "", step.attempt, state="failed", error="e" * 5000,
        input_tokens=120, output_tokens=30, cost_usd=0.25,
    )
    assert done.state == "failed"
    assert done.agent_id == "agent-9"
    assert len(done.error or "") == 2000
    assert (done.input_tokens, done.output_tokens) == (120, 30)
    assert done.cost_usd == Decimal("0.25")
    assert done.finished_at is not None
    ok = store.start_step(run_id="run-1", step_id="t", input_hash="2" * 64)
    finished = store.finish_step("run-1", "t", "", ok.attempt, state="succeeded", output={"a": [1, 2], "b": "ç"}, agent_id="agent-10")
    assert finished.output == {"a": [1, 2], "b": "ç"}
    assert finished.agent_id == "agent-10"
    assert finished.cost_usd is None  # unknown stays unknown, never zero


def test_invalid_transitions_are_refused(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    step = store.start_step(run_id="run-1", step_id="s", input_hash="1" * 64)
    store.finish_step("run-1", "s", "", step.attempt, state="succeeded")
    with pytest.raises(PipelineStoreError):
        store.finish_step("run-1", "s", "", step.attempt, state="running")
    with pytest.raises(PipelineStoreError):
        store.finish_step("run-1", "s", "", step.attempt, state="failed")
    with pytest.raises(PipelineStoreError):
        store.finish_step("run-1", "s", "", 99, state="succeeded")
    with pytest.raises(PipelineStoreError):
        store.finish_step("run-1", "s", "", step.attempt, state="bogus")
    with pytest.raises(PipelineStoreError):
        store.start_step(run_id="missing", step_id="s", input_hash="1" * 64)


def test_run_state_transitions_and_stop_reason(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    store.set_run_state("run-1", "interrupted", stop_reason="paused")
    run = store.get_run("run-1")
    assert run is not None and run.state == "interrupted" and run.stop_reason == "paused"
    store.set_run_state("run-1", "running")
    run = store.get_run("run-1")
    assert run is not None and run.state == "running" and run.finished_at is None and run.stop_reason is None
    store.set_run_state("run-1", "succeeded")
    run = store.get_run("run-1")
    assert run is not None and run.finished_at is not None
    with pytest.raises(PipelineStoreError):
        store.set_run_state("run-1", "running")
    with pytest.raises(PipelineStoreError):
        store.set_run_state("run-1", "weird")
    with pytest.raises(PipelineStoreError):
        store.set_run_state("missing", "failed")


def test_add_usage_accumulates_on_the_run(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    store.add_usage("run-1", tokens=100, cost_usd=0.5, seconds=3)
    store.add_usage("run-1", tokens=50, cost_usd=None, seconds=2)
    store.add_usage("run-1", tokens=0, cost_usd=Decimal("0.125"), seconds=0)
    run = store.get_run("run-1")
    assert run is not None
    assert run.used_tokens == 150
    assert run.used_cost_usd == Decimal("0.625")
    assert run.used_seconds == 5


def test_interrupt_orphans_turns_running_into_interrupted(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace, run_id="a")
    _run(store, workspace, run_id="b")
    _run(store, workspace, run_id="done")
    store.set_run_state("done", "succeeded")
    for run_id in ("a", "b"):
        store.start_step(run_id=run_id, step_id="s", input_hash="1" * 64)
    finished = store.start_step(run_id="a", step_id="t", input_hash="2" * 64)
    store.finish_step("a", "t", "", finished.attempt, state="succeeded")

    assert store.interrupt_orphans("a") == 1
    run_a, run_b = store.get_run("a"), store.get_run("b")
    assert run_a is not None and run_a.state == "interrupted"
    assert run_b is not None and run_b.state == "running"
    states_a = {step.step_id: step.state for step in store.steps_of("a")}
    assert states_a == {"s": "interrupted", "t": "succeeded"}

    assert store.interrupt_orphans(None) == 1
    run_b = store.get_run("b")
    assert run_b is not None and run_b.state == "interrupted"
    done = store.get_run("done")
    assert done is not None and done.state == "succeeded"
    assert store.interrupt_orphans(None) == 0


def test_usage_aggregation_by_step_agent_and_provider(engine, store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        for agent_id, provider in (("ag1", "anthropic-cli"), ("ag2", "openai-codex")):
            connection.execute(insert(agent_runs).values(
                agent_id=agent_id, user_id="u1", conversation_id="c1", turn_id="t1", subagent_type="general",
                provider=provider, model_id="m", state="completed", last_activity_at=now, created_at=now,
            ))
    plan = (
        ("impl", "", "ag1", 100, 10, "0.5"),
        ("impl", "", "ag1", 50, 5, "0.25"),
        ("rev", "x", "ag2", 200, 20, None),
        ("rev", "y", "ag2", 10, 1, "0.1"),
    )
    for step_id, item_key, agent_id, tin, tout, cost in plan:
        started = store.start_step(run_id="run-1", step_id=step_id, item_key=item_key, input_hash="1" * 64, agent_id=agent_id)
        store.finish_step(
            "run-1", step_id, item_key, started.attempt, state="succeeded",
            input_tokens=tin, output_tokens=tout, cost_usd=None if cost is None else Decimal(cost),
        )
    by_step = {row["key"]: row for row in store.usage_by("run-1", "step")}
    assert by_step["impl"]["input_tokens"] == 150 and by_step["impl"]["output_tokens"] == 15
    assert by_step["impl"]["cost_usd"] == Decimal("0.75") and by_step["impl"]["cost_known"] is True
    assert by_step["rev"]["input_tokens"] == 210
    assert by_step["rev"]["cost_usd"] == Decimal("0.1") and by_step["rev"]["cost_known"] is False
    by_agent = {row["key"]: row for row in store.usage_by("run-1", "agent")}
    assert set(by_agent) == {"ag1", "ag2"}
    assert by_agent["ag1"]["rows"] == 2
    by_provider = {row["key"]: row for row in store.usage_by("run-1", "provider")}
    assert set(by_provider) == {"anthropic-cli", "openai-codex"}
    assert by_provider["openai-codex"]["input_tokens"] == 210
    with pytest.raises(PipelineStoreError):
        store.usage_by("run-1", "color")


def test_large_output_goes_to_an_atomic_artifact_file(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    step = store.start_step(run_id="run-1", step_id="pesquisa", item_key="fonte/1", input_hash="1" * 64)
    payload = {"texto": "ã" * ARTIFACT_THRESHOLD_BYTES}
    done = store.finish_step("run-1", "pesquisa", "fonte/1", step.attempt, state="succeeded", output=payload)
    assert set(done.output or {}) == {"ref"}
    ref = done.output["ref"]
    assert not ref.startswith(("/", "..")) and "\\" not in ref and ".." not in ref.split("/")
    artifact = workspace / ref
    assert artifact.is_file()
    assert artifact.relative_to(workspace / ".orin" / "pipeline-runs" / "run-1")
    assert store.load_output("run-1", done.output) == payload
    assert not list(artifact.parent.glob("*.tmp"))
    small = store.start_step(run_id="run-1", step_id="s2", input_hash="1" * 64)
    done_small = store.finish_step("run-1", "s2", "", small.attempt, state="succeeded", output={"ok": 1})
    assert done_small.output == {"ok": 1}
    assert store.load_output("run-1", done_small.output) == {"ok": 1}


def test_artifacts_of_different_attempts_do_not_overwrite_each_other(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    refs = []
    for number in range(2):
        step = store.start_step(run_id="run-1", step_id="s", input_hash="1" * 64)
        done = store.finish_step(
            "run-1", "s", "", step.attempt, state="succeeded",
            output={"n": number, "pad": "x" * ARTIFACT_THRESHOLD_BYTES},
        )
        refs.append(done.output["ref"])
    assert refs[0] != refs[1]
    assert store.load_output("run-1", {"ref": refs[0]})["n"] == 0
    assert store.load_output("run-1", {"ref": refs[1]})["n"] == 1


def test_load_output_refuses_refs_outside_the_run_directory(store: PipelineStore, workspace: Path) -> None:
    _run(store, workspace)
    (workspace / "secret.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PipelineStoreError):
        store.load_output("run-1", {"ref": "../secret.json"})
    with pytest.raises(PipelineStoreError):
        store.load_output("run-1", {"ref": "secret.json"})
