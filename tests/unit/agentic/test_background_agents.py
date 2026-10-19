"""W5: a background agent writes its report to an output file, and TaskStop can stop it."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN, _RecordingStore, _parallel_tool_calls_batch, _text_batch,
)


class _QuickChild:
    def stream(self, _request):
        return iter(_text_batch("Background report: all good."))


class _EndlessChild:
    """Keeps asking for another listing until the run is cancelled."""

    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()

    def stream(self, _request):
        self.calls += 1
        self.started.set()
        time.sleep(0.05)
        return iter(_parallel_tool_calls_batch([(f"call-{self.calls}", "list_files", {"path": "."})]))


def _session(tmp_path: Path, child) -> tuple[TurnSession, object]:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'bg.db').as_posix()}")
    metadata.create_all(engine)
    session = TurnSession(
        turn=dict(TURN), store=_RecordingStore(),
        agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
        memory_store=None, provider_factory=lambda: child, workspace_root=tmp_path, child_provider_factory=lambda _model: child,
    )
    return session, engine


def test_a_background_agent_writes_its_report_to_an_output_file(tmp_path: Path) -> None:
    session, engine = _session(tmp_path, _QuickChild())
    try:
        started = session._run_subagent(subagent_type="general-purpose", task="Summarise", run_in_background=True)
        assert started.status == "succeeded", started.content
        agent_id, output_file = started.payload["agent_id"], started.payload["output_file"]
        assert output_file.startswith(".orin/agent-outputs/") and output_file in started.content
        done = session._check_subagent(agent_id, wait_seconds=20)
        assert done.status == "succeeded", done.content
        assert "Background report: all good." in (session.workspace.root / output_file).read_text(encoding="utf-8")
    finally:
        session.close_background_subagents()
        engine.dispose()


def test_taskstop_stops_a_running_background_agent(tmp_path: Path) -> None:
    child = _EndlessChild()
    session, engine = _session(tmp_path, child)
    try:
        started = session._run_subagent(subagent_type="general-purpose", task="Loop", run_in_background=True)
        agent_id = started.payload["agent_id"]
        assert child.started.wait(10)
        toolset = AgentToolset(ConversationWorkspace(tmp_path / "other", "c1"), public_tool_names=True)
        toolset._stop_subagent = session._stop_background_subagent
        stopped = toolset.invoke("TaskStop", {"task_id": agent_id})
        assert stopped.status == "succeeded", stopped.content
        assert stopped.data["tool_output"]["task_type"] == "local_agent"
        result = session._check_subagent(agent_id, wait_seconds=20)
        assert result.payload["status"] != "running"
        calls = child.calls
        time.sleep(0.5)
        assert child.calls == calls  # nothing runs after the stop
    finally:
        session.close_background_subagents()
        engine.dispose()


def test_stopping_an_unknown_or_finished_agent_says_so(tmp_path: Path) -> None:
    session, engine = _session(tmp_path, _QuickChild())
    try:
        assert session._stop_background_subagent("agent:nope").status == "failed"
        started = session._run_subagent(subagent_type="general-purpose", task="Quick", run_in_background=True)
        session._check_subagent(started.payload["agent_id"], wait_seconds=20)
        finished = session._stop_background_subagent(started.payload["agent_id"])
        assert finished.status == "succeeded" and "already finished" in finished.content
    finally:
        session.close_background_subagents()
        engine.dispose()


def test_without_agent_support_a_name_is_still_refused(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True)
    assert toolset.invoke("TaskStop", {"task_id": "agent:x"}).status == "failed"
