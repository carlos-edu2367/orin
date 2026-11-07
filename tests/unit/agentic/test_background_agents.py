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


def test_registry_finish_wakes_wait_any() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    entry = BackgroundAgentEntry(
        agent_id="agent:c1:1",
        conversation_id="c1",
        name="codex",
        output_file=".orin/agent-outputs/agent_c1_1.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)
    assert len(reg.running("c1")) == 1

    def _finish_soon() -> None:
        time.sleep(0.05)
        reg.finish("agent:c1:1", "completed", "Done line 1\nDone line 2")

    t = threading.Thread(target=_finish_soon, daemon=True)
    t0 = time.monotonic()
    t.start()
    woke = reg.wait_any("c1", timeout=2.0, cancelled=lambda: False)
    elapsed = time.monotonic() - t0
    t.join(timeout=1.0)
    assert woke is True
    assert elapsed < 1.0
    assert reg.running("c1") == []
    got = reg.get("c1", "agent:c1:1")
    assert got is not None
    assert got.status == "completed"
    assert got.summary == "Done line 1"


def test_registry_take_unnotified_delivers_once() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    entry = BackgroundAgentEntry(
        agent_id="agent:c1:2",
        conversation_id="c1",
        name="antigravity",
        output_file=".orin/agent-outputs/agent_c1_2.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)
    assert reg.has_unnotified("c1") is False

    reg.finish("agent:c1:2", "completed", "Summary text")
    assert reg.has_unnotified("c1") is True
    first = reg.take_unnotified("c1")
    assert [e.agent_id for e in first] == ["agent:c1:2"]
    assert first[0].notified is True
    assert reg.has_unnotified("c1") is False
    assert reg.take_unnotified("c1") == []


def test_registry_wait_any_returns_on_cancelled_or_extra_wake() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    entry = BackgroundAgentEntry(
        agent_id="agent:c1:3",
        conversation_id="c1",
        name="codex",
        output_file=".orin/agent-outputs/agent_c1_3.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)

    cancel_flag = threading.Event()
    threading.Timer(0.05, cancel_flag.set).start()
    t0 = time.monotonic()
    reg.wait_any("c1", timeout=5.0, cancelled=cancel_flag.is_set)
    assert (time.monotonic() - t0) <= 0.6

    wake_flag = threading.Event()
    threading.Timer(0.05, wake_flag.set).start()
    t1 = time.monotonic()
    reg.wait_any("c1", timeout=5.0, cancelled=lambda: False, extra_wake=wake_flag.is_set)
    assert (time.monotonic() - t1) <= 0.6


def test_registry_isolates_different_conversations() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    e1 = BackgroundAgentEntry(
        agent_id="agent:c1:1",
        conversation_id="c1",
        name="codex",
        output_file=".orin/agent-outputs/1.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    e2 = BackgroundAgentEntry(
        agent_id="agent:c2:1",
        conversation_id="c2",
        name="antigravity",
        output_file=".orin/agent-outputs/2.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(e1)
    reg.register(e2)
    reg.finish("agent:c2:1", "completed", "c2 done")

    assert len(reg.running("c1")) == 1
    assert len(reg.running("c2")) == 0
    assert reg.get("c1", "agent:c2:1") is None
    assert reg.has_unnotified("c1") is False
    assert reg.has_unnotified("c2") is True


def test_session_b_check_subagent_reads_completed_agent_from_session_a(tmp_path: Path) -> None:
    from agentos.agentic.background_agents import BACKGROUND_AGENTS, fail_orphaned_running_agents

    gate = threading.Event()

    class _DelayedChild:
        def stream(self, _request):
            gate.wait(timeout=5.0)
            return iter(_text_batch("Cross-turn report ready."))

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'cross.db').as_posix()}")
    metadata.create_all(engine)
    try:
        session_a = TurnSession(
            turn=dict(TURN),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: _DelayedChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _DelayedChild(),
        )
        started = session_a._run_subagent(subagent_type="general-purpose", task="Work across turns", run_in_background=True)
        assert started.status == "succeeded"
        agent_id = started.payload["agent_id"]

        # Session A closes while child is still running; child must survive and finish.
        session_a.close_background_subagents()
        gate.set()

        session_b = TurnSession(
            turn={**TURN, "turn_id": "turn-2"},
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: _DelayedChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _DelayedChild(),
        )
        done = session_b._check_subagent(agent_id, wait_seconds=10)
        assert done.status == "succeeded", done.content
        assert done.payload["status"] == "completed"
        assert "Cross-turn report ready." in done.content

        # Also test fallback to DB + output file when in-memory registry does not have the entry (process restart)
        BACKGROUND_AGENTS.clear_for_tests()
        done_from_disk = session_b._check_subagent(agent_id, wait_seconds=0)
        assert done_from_disk.status == "succeeded", done_from_disk.content
        assert done_from_disk.payload["status"] == "completed"
        assert "Cross-turn report ready." in done_from_disk.content

        # And test startup sweep of orphaned running/working agents in DB
        session_b.agents_store.set_state(agent_id, "working")
        swept = fail_orphaned_running_agents(engine, workspace_root_resolver=lambda _cid: session_b.workspace.root)
        assert agent_id in swept
        row = session_b.agents_store.get(agent_id)
        assert row is not None and row["state"] == "failed"
    finally:
        gate.set()
        session_a.close_background_subagents()
        engine.dispose()

