"""Telemetry aggregation over the pipeline journal (WS-C5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert

from agentos.persistence.postgres.schema import agent_runs, metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.store import PipelineStore
from agentos.pipelines.telemetry import run_summary


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def store(engine) -> PipelineStore:
    return PipelineStore(engine)


def _run(store: PipelineStore, tmp_path: Path, run_id: str = "run-1") -> None:
    store.create_run(
        run_id=run_id, user_id="u1", conversation_id="c1", workspace_root=(tmp_path / "ws").as_posix(),
        pipeline_id="p", definition_hash="h" * 64, source="user", inputs={},
    )


def _agent(engine, agent_id: str, provider: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(insert(agent_runs).values(
            agent_id=agent_id, user_id="u1", conversation_id="c1", turn_id="t1", subagent_type="general",
            provider=provider, model_id="m", state="completed", last_activity_at=now, created_at=now,
        ))


def _step(store, step_id, *, item="", agent=None, tin=0, tout=0, cost=None, state="succeeded", loop=0):
    started = store.start_step(run_id="run-1", step_id=step_id, item_key=item, input_hash="1" * 64, loop_count=loop, agent_id=agent)
    return store.finish_step(
        "run-1", step_id, item, started.attempt, state=state, input_tokens=tin, output_tokens=tout, cost_usd=cost,
    )


def test_summary_aggregates_by_step_agent_provider_and_total(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _agent(engine, "ag1", "anthropic-cli")
    _agent(engine, "ag2", "openai-codex")
    _step(store, "impl", agent="ag1", tin=100, tout=20, cost=Decimal("0.5"))
    _step(store, "rev", item="a", agent="ag2", tin=50, tout=10, cost=Decimal("0.25"))
    _step(store, "rev", item="b", agent="ag2", tin=30, tout=5, cost=Decimal("0.125"))
    _step(store, "gate")  # a command gate: no agent, no cost
    summary = run_summary(store, "run-1")

    assert summary.total.input_tokens == 180
    assert summary.total.output_tokens == 35
    assert summary.total.total_tokens == 215
    assert summary.total.cost_usd == pytest.approx(0.875)
    assert summary.total.cost_known is True
    by_step = {row.key: row for row in summary.by_step}
    assert set(by_step) == {"impl", "rev", "gate"}
    assert by_step["rev"].input_tokens == 80 and by_step["rev"].attempts == 2
    assert by_step["gate"].cost_usd == 0 and by_step["gate"].cost_known is True
    assert {row.key for row in summary.by_agent} == {"ag1", "ag2"}
    by_provider = {row.key: row for row in summary.by_provider}
    assert by_provider["openai-codex"].input_tokens == 80
    assert by_provider["anthropic-cli"].cost_usd == pytest.approx(0.5)


def test_unknown_cost_is_null_with_cost_known_false_never_zero(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _agent(engine, "ag1", "openai-codex")
    _step(store, "impl", agent="ag1", tin=100, tout=20, cost=None)
    summary = run_summary(store, "run-1")
    assert summary.total.cost_usd is None
    assert summary.total.cost_known is False
    assert summary.by_step[0].cost_usd is None and summary.by_step[0].cost_known is False
    assert summary.by_provider[0].cost_usd is None
    payload = summary.to_dict()
    assert payload["total"]["cost_usd"] is None and payload["total"]["cost_known"] is False


def test_partial_cost_is_a_lower_bound_and_flagged(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _agent(engine, "ag1", "anthropic-cli")
    _agent(engine, "ag2", "openai-codex")
    _step(store, "a", agent="ag1", tin=1, cost=Decimal("0.4"))
    _step(store, "b", agent="ag2", tin=1, cost=None)
    summary = run_summary(store, "run-1")
    assert summary.total.cost_usd == pytest.approx(0.4)
    assert summary.total.cost_known is False


def test_a_reused_step_is_counted_once_and_a_skipped_step_counts_zero(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _agent(engine, "ag1", "anthropic-cli")
    first = _step(store, "impl", agent="ag1", tin=100, tout=10, cost=Decimal("0.5"))
    # A resume that reuses the step writes no new row: the engine only emits `pipeline.step_reused`.
    assert store.find_reusable("run-1", "impl", "", 0).attempt == first.attempt
    skipped = store.start_step(run_id="run-1", step_id="opcional", input_hash="2" * 64, state="skipped")
    assert skipped.state == "skipped"
    summary = run_summary(store, "run-1")
    assert summary.total.total_tokens == 110
    by_step = {row.key: row for row in summary.by_step}
    assert by_step["impl"].attempts == 1
    assert by_step["opcional"].total_tokens == 0 and by_step["opcional"].cost_usd == 0 and by_step["opcional"].cost_known


def test_retries_are_all_counted_because_each_attempt_spent_tokens(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _agent(engine, "ag1", "anthropic-cli")
    _step(store, "impl", agent="ag1", tin=100, cost=Decimal("0.5"), state="failed")
    _step(store, "impl", agent="ag1", tin=120, cost=Decimal("0.6"), state="succeeded")
    summary = run_summary(store, "run-1")
    assert summary.total.input_tokens == 220
    assert summary.by_step[0].attempts == 2
    assert summary.by_step[0].latest_states == {"succeeded": 1}


def test_run_used_matches_the_sum_of_steps_and_flags_drift(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _agent(engine, "ag1", "anthropic-cli")
    _step(store, "a", agent="ag1", tin=60, tout=40, cost=Decimal("0.3"))
    _step(store, "b", agent="ag1", tin=10, tout=5, cost=Decimal("0.2"))
    store.add_usage("run-1", tokens=100, cost_usd=Decimal("0.3"), seconds=4)
    store.add_usage("run-1", tokens=15, cost_usd=Decimal("0.2"), seconds=2)
    summary = run_summary(store, "run-1")
    assert summary.run_used.tokens == summary.total.total_tokens == 115
    assert summary.run_used.cost_usd == pytest.approx(summary.total.cost_usd)
    assert summary.run_used.seconds == 6
    assert summary.consistent is True

    store.add_usage("run-1", tokens=999)  # drift: the run counter ran ahead of the journal
    assert run_summary(store, "run-1").consistent is False
    store.recompute_usage("run-1")
    assert run_summary(store, "run-1").consistent is True


def test_summary_is_json_serialisable_and_carries_run_identity(engine, store: PipelineStore, tmp_path: Path) -> None:
    _run(store, tmp_path)
    _step(store, "a")
    store.set_run_state("run-1", "failed", stop_reason="step_failed")
    payload = run_summary(store, "run-1").to_dict()
    json.dumps(payload)
    assert payload["run_id"] == "run-1"
    assert payload["state"] == "failed" and payload["stop_reason"] == "step_failed"
    assert payload["pipeline_id"] == "p"
    assert set(payload) >= {"total", "by_step", "by_agent", "by_provider", "run_used", "consistent"}


def test_unknown_run_raises_a_clear_error(store: PipelineStore) -> None:
    with pytest.raises(LookupError):
        run_summary(store, "nope")
