"""WS-B5 tests: ProgressTracker throttling, current_tool lifecycle, subtree usage aggregation, and batched DB touch."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agentos.agentic.agent_progress import AgentProgressSnapshot, ProgressTracker
from agentos.agentic.agent_run_persistence import AgentRunRecorder
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


class _FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now_dt = start or datetime(2026, 11, 21, 14, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.now_dt

    def monotonic(self) -> float:
        return self.now_dt.timestamp()

    def advance(self, seconds: float) -> None:
        self.now_dt = self.now_dt + timedelta(seconds=seconds)


def test_agent_progress_event_throttled_to_at_most_once_per_10s_per_agent() -> None:
    clock = _FakeClock()
    emitted: list[AgentProgressSnapshot] = []
    tracker = ProgressTracker(
        clock=clock.monotonic,
        wall_clock=clock.now,
        on_progress=emitted.append,
        progress_interval_seconds=10.0,
    )

    tracker.start("agent:1", conversation_id="conv-1", parent_agent_id="agent:conv-1:main")
    # First tool start at t=0 emits immediately (1st event)
    tracker.on_tool_start("agent:1", "Read")
    assert len(emitted) == 1

    # Rapid updates inside the 10s window are suppressed
    clock.advance(2.0)
    tracker.on_tool_finish("agent:1", "Read")
    clock.advance(3.0)
    tracker.on_tool_start("agent:1", "Grep")
    clock.advance(4.0)  # t = 9.0s
    tracker.on_usage("agent:1", input_tokens=100, output_tokens=50, cost_usd=0.01)
    assert len(emitted) == 1

    # At t = 10.5s (>= 10s since last emit) -> emits 2nd event
    clock.advance(1.5)
    tracker.on_activity("agent:1")
    assert len(emitted) == 2

    # Another agent has an independent 10s throttle window
    tracker.start("agent:2", conversation_id="conv-1", parent_agent_id="agent:conv-1:main")
    tracker.on_tool_start("agent:2", "Bash")
    assert len(emitted) == 3
    assert emitted[-1].agent_id == "agent:2"

    # agent:1 still throttled at t = 15s (4.5s since its 2nd emit)
    clock.advance(4.5)
    tracker.on_activity("agent:1")
    assert len(emitted) == 3

    # At t = 21s (>= 10s since 10.5s) -> emits 3rd event for agent:1
    clock.advance(6.0)
    tracker.on_activity("agent:1")
    assert len(emitted) == 4
    assert emitted[-1].agent_id == "agent:1"


def test_current_tool_reflects_active_tool_and_clears_on_finish() -> None:
    clock = _FakeClock()
    tracker = ProgressTracker(clock=clock.monotonic, wall_clock=clock.now)
    tracker.start("agent:t", conversation_id="conv-1")

    snap0 = tracker.snapshot("agent:t")
    assert snap0 is not None
    assert snap0.current_tool is None
    assert snap0.tool_calls == 0

    clock.advance(3.0)
    tracker.on_tool_start("agent:t", "Edit")
    snap1 = tracker.snapshot("agent:t")
    assert snap1 is not None
    assert snap1.current_tool == "Edit"
    assert snap1.tool_calls == 1
    assert snap1.elapsed_seconds == 3.0

    clock.advance(4.0)
    tracker.on_tool_finish("agent:t", "Edit")
    snap2 = tracker.snapshot("agent:t")
    assert snap2 is not None
    assert snap2.current_tool is None
    assert snap2.tool_calls == 1
    assert snap2.elapsed_seconds == 7.0
    assert snap2.last_activity_at == clock.now()


def test_tokens_and_cost_aggregate_child_and_grandchildren_subtree() -> None:
    clock = _FakeClock()
    tracker = ProgressTracker(clock=clock.monotonic, wall_clock=clock.now)

    tracker.start("child-1", conversation_id="conv-1", parent_agent_id="main")
    tracker.start("grandchild-1", conversation_id="conv-1", parent_agent_id="child-1")
    tracker.start("grandchild-2", conversation_id="conv-1", parent_agent_id="child-1")
    tracker.start("greatgrand-1", conversation_id="conv-1", parent_agent_id="grandchild-1")
    tracker.start("sibling-1", conversation_id="conv-1", parent_agent_id="main")

    tracker.on_usage("child-1", input_tokens=100, output_tokens=40, cost_usd=0.01)
    tracker.on_usage("grandchild-1", input_tokens=200, output_tokens=60, cost_usd=0.02)
    tracker.on_usage("grandchild-2", input_tokens=300, output_tokens=80, cost_usd=0.03)
    tracker.on_usage("greatgrand-1", input_tokens=50, output_tokens=20, cost_usd=0.005)
    # Unrelated sibling must NOT be summed into child-1
    tracker.on_usage("sibling-1", input_tokens=999, output_tokens=999, cost_usd=9.99)

    snap_child = tracker.snapshot("child-1")
    assert snap_child is not None
    assert snap_child.input_tokens == 100 + 200 + 300 + 50
    assert snap_child.output_tokens == 40 + 60 + 80 + 20
    assert abs(snap_child.cost_usd - 0.065) < 1e-9

    snap_gc1 = tracker.snapshot("grandchild-1")
    assert snap_gc1 is not None
    assert snap_gc1.input_tokens == 250
    assert snap_gc1.output_tokens == 80
    assert abs(snap_gc1.cost_usd - 0.025) < 1e-9


def test_tracker_batches_agent_runs_touch_to_5s_and_flushes_on_finish(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'prog_batch.db').as_posix()}")
    metadata.create_all(engine)
    clock = _FakeClock()
    try:
        store = AgentRunStore(engine, clock=clock.now)
        recorder = AgentRunRecorder(
            engine,
            runs_dir=tmp_path / "agent-runs",
            store=store,
            clock=clock.now,
            monotonic=clock.monotonic,
            min_touch_interval_seconds=5.0,
        )
        recorder.start(
            agent_id="agent:batched",
            user_id="user-1",
            conversation_id="conv-1",
            turn_id="turn-1",
            subagent_type="general",
            state="running",
        )
        tracker = ProgressTracker(
            clock=clock.monotonic,
            wall_clock=clock.now,
            recorder=recorder,
        )
        tracker.start("agent:batched", conversation_id="conv-1")

        touch_count = 0
        real_touch = store.touch

        def _spy_touch(*args, **kwargs):
            nonlocal touch_count
            touch_count += 1
            return real_touch(*args, **kwargs)

        with patch.object(store, "touch", side_effect=_spy_touch):
            clock.advance(1.0)
            tracker.on_tool_start("agent:batched", "Read")
            clock.advance(2.0)
            tracker.on_tool_finish("agent:batched", "Read")
            clock.advance(1.0)  # t = 4.0s -> still within 5s
            tracker.on_usage("agent:batched", input_tokens=120, output_tokens=30, cost_usd=0.01)
            assert touch_count == 0

            clock.advance(1.5)  # t = 5.5s -> flushes once
            tracker.on_tool_start("agent:batched", "Write")
            assert touch_count == 1

            # Finish at t = 6.5s flushes unconditionally
            clock.advance(1.0)
            tracker.finish("agent:batched", state="completed")
            assert touch_count == 2

        row = store.get("agent:batched")
        assert row is not None
        assert row.state == "completed"
        assert row.tool_calls == 2
        assert row.input_tokens == 120
        assert row.output_tokens == 30
    finally:
        engine.dispose()
