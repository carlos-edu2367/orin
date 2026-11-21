"""WS-B5 tests: Criterion 7 (stall detection at 11 min with injected clock, autoCancel=False vs True)."""
from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.loader import EffectiveSettings, SettingsLayer
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore


class _FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now_dt = start or datetime(2026, 11, 21, 16, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.now_dt

    def monotonic(self) -> float:
        return self.now_dt.timestamp()

    def advance(self, seconds: float) -> None:
        self.now_dt = self.now_dt + timedelta(seconds=seconds)


def _settings(data: dict) -> EffectiveSettings:
    return EffectiveSettings(
        data=data,
        provenance={},
        layers=(SettingsLayer(name="user", path=None, data=data),),
        errors=(),
        trusted=True,
    )


def _setup_stalling_session(tmp_path: Path, clock: _FakeClock, settings_data: dict):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'stall.db').as_posix()}")
    metadata.create_all(engine)
    rec_store = _RecordingStore()
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    session = TurnSession(
        turn=dict(TURN),
        store=rec_store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
    )
    session.effective_settings = _settings(settings_data)
    session._agent_runs_dir = tmp_path / "agent-runs"
    session._progress_clock = clock.monotonic
    session._progress_wall_clock = clock.now

    rec = agents_store.create("stalled-worker", "general", parent_agent_id=session.main_agent_id)
    agent_id = str(rec["agent_id"])
    agents_store.set_state(agent_id, "working")

    recorder = session._agent_run_recorder()
    assert recorder is not None
    recorder.start(
        agent_id=agent_id,
        user_id=TURN["user_id"],
        conversation_id=TURN["conversation_id"],
        turn_id=TURN["turn_id"],
        parent_agent_id=session.main_agent_id,
        subagent_type="general",
        state="running",
    )

    cancel_ev = threading.Event()
    fut: Future[ToolOutcome] = Future()
    session._agent_tree()[agent_id] = session.main_agent_id
    with session._subagent_lock:
        session._background_subagents[agent_id] = {
            "future": fut,
            "agent_id": agent_id,
            "agent_name": "stalled-worker",
            "subagent_type": "general",
            "cancel": cancel_ev,
            "output_file": session._agent_output_file(agent_id),
        }

    tracker = session._progress_tracker()
    tracker.start(agent_id, conversation_id=TURN["conversation_id"], parent_agent_id=session.main_agent_id)
    return session, engine, rec_store, agent_id, cancel_ev


def test_criterion_7_check_subagent_reports_stalled_after_11_min_without_cancelling_by_default(tmp_path: Path) -> None:
    """Critério 7: with orchestration.stall.minutes=10 and autoCancel=False (default),
    an agent idle for 11 min returns stalled: True, emits warning/event, and does NOT cancel."""
    clock = _FakeClock()
    BACKGROUND_AGENTS.clear_for_tests()
    session, engine, rec_store, agent_id, cancel_ev = _setup_stalling_session(
        tmp_path,
        clock,
        {"orchestration": {"stall": {"minutes": 10, "autoCancel": False}}},
    )
    try:
        # At 9 min 59 s -> NOT stalled yet
        clock.advance(9 * 60 + 59)
        early = session._check_subagent(agent_id, wait_seconds=0)
        assert early.payload["stalled"] is False
        assert early.payload["state"] == "running"
        assert not cancel_ev.is_set()

        # Advance to 11 min total (no sleep!) -> stalled: True, NOT cancelled
        clock.advance(61)
        stalled_out = session._check_subagent(agent_id, wait_seconds=0)
        assert stalled_out.payload["stalled"] is True
        assert stalled_out.payload["state"] == "stalled"
        assert not cancel_ev.is_set()

        # agent_runs updated to stalled
        run = AgentRunStore(engine).get(agent_id)
        assert run is not None
        assert run.state == "stalled"

        # Stall event recorded on session activity stream
        recorded_types = [ev[0] for ev in rec_store.activity]
        assert AgentActivityEventType.AGENT_STALLED in recorded_types

        # If activity resumes later, stalled clears back to running
        session._progress_tracker().on_activity(agent_id)
        recovered = session._check_subagent(agent_id, wait_seconds=0)
        assert recovered.payload["stalled"] is False
        assert recovered.payload["state"] == "running"
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


def test_stall_auto_cancel_true_cancels_agent_via_stop_path_and_records_cancelled(tmp_path: Path) -> None:
    """With orchestration.stall.autoCancel=True, stalling triggers the same stop path and records cancelled."""
    clock = _FakeClock()
    BACKGROUND_AGENTS.clear_for_tests()
    session, engine, _rec_store, agent_id, cancel_ev = _setup_stalling_session(
        tmp_path,
        clock,
        {"orchestration": {"stall": {"minutes": 10, "autoCancel": True}}},
    )
    try:
        clock.advance(11 * 60)
        out = session._check_subagent(agent_id, wait_seconds=0)
        assert cancel_ev.is_set(), "autoCancel=True must trigger the background stop cancel event"
        assert out.payload["stalled"] is True
        assert out.payload["state"] == "cancelled"

        run = AgentRunStore(engine).get(agent_id)
        assert run is not None
        assert run.state == "cancelled"
        assert run.finished_at is not None
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()
