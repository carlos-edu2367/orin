"""Pruning of finished pipeline runs (WS-C3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from agentos.persistence.postgres.schema import metadata, pipeline_runs
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.store import ARTIFACT_THRESHOLD_BYTES, PIPELINE_RETENTION_DAYS, PipelineStore
from agentos.workers import scheduler as scheduler_mod


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(eng)
    yield eng
    eng.dispose()


def _make(store: PipelineStore, workspace: Path, run_id: str, state: str, age_days: int, *, engine) -> None:
    store.create_run(
        run_id=run_id, user_id="u1", conversation_id="c1", workspace_root=workspace.as_posix(),
        pipeline_id="p", definition_hash="h" * 64, source="user", inputs={},
    )
    step = store.start_step(run_id=run_id, step_id="s", input_hash="1" * 64)
    store.finish_step(run_id, "s", "", step.attempt, state="succeeded", output={"pad": "x" * ARTIFACT_THRESHOLD_BYTES})
    if state != "running":
        store.set_run_state(run_id, state)
    old = datetime.now(UTC) - timedelta(days=age_days)
    values = {"started_at": old - timedelta(minutes=1)}
    if state != "running":
        values["finished_at"] = old
    with engine.begin() as connection:
        connection.execute(update(pipeline_runs).where(pipeline_runs.c.run_id == run_id).values(**values))


def test_retention_is_ninety_days() -> None:
    assert PIPELINE_RETENTION_DAYS == 90


def test_prune_removes_old_finished_runs_steps_and_artifacts(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _make(store, workspace, "old-ok", "succeeded", 120, engine=engine)
    _make(store, workspace, "old-failed", "failed", 100, engine=engine)
    _make(store, workspace, "old-cancelled", "cancelled", 91, engine=engine)
    _make(store, workspace, "old-budget", "budget_exceeded", 95, engine=engine)
    _make(store, workspace, "recent", "succeeded", 10, engine=engine)
    _make(store, workspace, "old-running", "running", 400, engine=engine)
    _make(store, workspace, "old-interrupted", "interrupted", 400, engine=engine)

    removed = store.prune(datetime.now(UTC) - timedelta(days=90))
    assert removed == 4
    assert store.get_run("old-ok") is None and store.steps_of("old-ok") == []
    for kept in ("recent", "old-running", "old-interrupted"):
        assert store.get_run(kept) is not None
        assert len(store.steps_of(kept)) == 1
    base = workspace / ".orin" / "pipeline-runs"
    assert not (base / "old-ok").exists()
    assert (base / "recent").is_dir()
    assert (base / "old-running").is_dir() and (base / "old-interrupted").is_dir()


def test_maybe_prune_pipeline_runs_obeys_the_interval(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _make(store, workspace, "old", "succeeded", 200, engine=engine)

    assert scheduler_mod._maybe_prune_pipeline_runs(store, now_mono=10.0, next_prune_mono=500.0) == 500.0
    assert store.get_run("old") is not None  # not due yet

    next_run = scheduler_mod._maybe_prune_pipeline_runs(store, now_mono=1000.0, next_prune_mono=500.0)
    assert next_run == 1000.0 + scheduler_mod.PIPELINE_PRUNE_INTERVAL_SECONDS
    assert scheduler_mod.PIPELINE_PRUNE_INTERVAL_SECONDS == 6 * 3600.0
    assert store.get_run("old") is None


def test_maybe_prune_pipeline_runs_never_raises(tmp_path: Path) -> None:
    class Broken:
        def prune(self, *_args, **_kwargs):
            raise RuntimeError("db gone")

    result = scheduler_mod._maybe_prune_pipeline_runs(Broken(), now_mono=5.0, next_prune_mono=0.0)
    assert result == 5.0 + scheduler_mod.PIPELINE_PRUNE_INTERVAL_SECONDS


def test_prune_never_touches_running_or_interrupted_runs_even_if_ancient(engine, tmp_path: Path) -> None:
    store = PipelineStore(engine)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _make(store, workspace, "r", "running", 1000, engine=engine)
    _make(store, workspace, "i", "interrupted", 1000, engine=engine)
    assert store.prune(datetime.now(UTC)) == 0
