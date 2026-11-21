"""WS-B5 tests: CheckSubagent returns rich progress fields for single and multi-agent checks."""
from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore


class _FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now_dt = start or datetime(2026, 11, 21, 15, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.now_dt

    def monotonic(self) -> float:
        return self.now_dt.timestamp()

    def advance(self, seconds: float) -> None:
        self.now_dt = self.now_dt + timedelta(seconds=seconds)


def _make_session(tmp_path: Path, clock: _FakeClock) -> tuple[TurnSession, object]:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'chk_prog.db').as_posix()}")
    metadata.create_all(engine)
    session = TurnSession(
        turn=dict(TURN),
        store=_RecordingStore(),
        agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
    )
    session._agent_runs_dir = tmp_path / "agent-runs"
    session._progress_clock = clock.monotonic
    session._progress_wall_clock = clock.now
    return session, engine


def test_check_subagent_returns_progress_fields_including_subtree_usage_and_multi_agent_payload(tmp_path: Path) -> None:
    clock = _FakeClock()
    BACKGROUND_AGENTS.clear_for_tests()
    session, engine = _make_session(tmp_path, clock)
    try:
        fut1: Future[ToolOutcome] = Future()
        fut2: Future[ToolOutcome] = Future()
        fut2.set_result(ToolOutcome("succeeded", "Done", "Child 2 finished.", {"agent_id": "agent:child2"}))

        session._agent_tree()["agent:child1"] = session.main_agent_id
        session._agent_tree()["agent:grandchild1"] = "agent:child1"
        session._agent_tree()["agent:child2"] = session.main_agent_id

        with session._subagent_lock:
            session._background_subagents["agent:child1"] = {
                "future": fut1,
                "agent_id": "agent:child1",
                "agent_name": "Worker-1",
                "subagent_type": "general",
                "output_file": session._agent_output_file("agent:child1"),
            }
            session._background_subagents["agent:child2"] = {
                "future": fut2,
                "agent_id": "agent:child2",
                "agent_name": "Worker-2",
                "subagent_type": "explore",
                "output_file": session._agent_output_file("agent:child2"),
            }

        tracker = session._progress_tracker()
        tracker.start("agent:child1", conversation_id=TURN["conversation_id"], parent_agent_id=session.main_agent_id)
        tracker.start("agent:grandchild1", conversation_id=TURN["conversation_id"], parent_agent_id="agent:child1")
        tracker.start("agent:child2", conversation_id=TURN["conversation_id"], parent_agent_id=session.main_agent_id)

        clock.advance(12.0)
        tracker.on_tool_start("agent:child1", "Bash")
        tracker.on_usage("agent:child1", input_tokens=400, output_tokens=150, cost_usd=0.015)
        tracker.on_usage("agent:grandchild1", input_tokens=100, output_tokens=50, cost_usd=0.005)
        tracker.on_tool_start("agent:child2", "Read")
        tracker.on_tool_finish("agent:child2", "Read")
        tracker.on_usage("agent:child2", input_tokens=200, output_tokens=80, cost_usd=0.008)
        tracker.finish("agent:child2", state="completed")

        clock.advance(8.0)

        # Single check for running agent:child1
        out1 = session._check_subagent("agent:child1", wait_seconds=0)
        assert out1.status == "succeeded"
        p1 = out1.payload
        assert p1["status"] == "running"
        assert p1["state"] == "running"
        assert p1["elapsed_seconds"] == 20.0
        assert p1["last_activity_at"] is not None
        assert p1["current_tool"] == "Bash"
        assert p1["tool_calls"] == 1
        assert p1["input_tokens"] == 500
        assert p1["output_tokens"] == 200
        assert abs(float(p1["cost_usd"]) - 0.02) < 1e-9
        assert p1["stalled"] is False

        # Multi-agent check preserves status/agent_ids/agents AND attaches progress fields to each agent entry
        toolset = session._toolset(subagents=True, code_mode_active=True)
        out_multi = toolset.invoke("CheckSubagent", {"agent_ids": ["agent:child1", "agent:child2"], "wait_seconds": 0})
        assert out_multi.status == "succeeded"
        assert out_multi.payload["status"] == "running"
        assert out_multi.payload["agent_ids"] == ["agent:child1", "agent:child2"]
        agents_by_id = {item["agent_id"]: item for item in out_multi.payload["agents"]}
        assert agents_by_id["agent:child1"]["current_tool"] == "Bash"
        assert agents_by_id["agent:child1"]["input_tokens"] == 500
        assert agents_by_id["agent:child1"]["stalled"] is False
        assert agents_by_id["agent:child2"]["state"] == "completed"
        assert agents_by_id["agent:child2"]["current_tool"] is None
        assert agents_by_id["agent:child2"]["tool_calls"] == 1
        assert agents_by_id["agent:child2"]["input_tokens"] == 200
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()
