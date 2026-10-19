"""W5: agent teams — named teammates, SendMessage between them and the lead, and TeammateIdle."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN, _RecordingStore, _parallel_tool_calls_batch, _text_batch,
)


class _Child:
    """Answers every request; can be told to call SendMessage first, or to hold its first run until released."""

    def __init__(self, *, message_lead: bool = False, hold: bool = False) -> None:
        self.requests: list[str] = []
        self.message_lead = message_lead
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.started = threading.Event()
        self._lock = threading.Lock()

    def stream(self, request):
        with self._lock:
            self.requests.append(repr(request))
            count = len(self.requests)
        self.started.set()
        self.release.wait(10)
        if self.message_lead and count == 1:
            return iter(_parallel_tool_calls_batch([("call-1", "send_message", {"to": "lead", "message": "Found the bug in parser.py"})]))
        return iter(_text_batch(f"Report {count}."))


@pytest.fixture()
def team(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'team.db').as_posix()}")
    metadata.create_all(engine)
    sessions: list[TurnSession] = []

    def make(child: _Child) -> TurnSession:
        bus = LifecycleBus()
        session = TurnSession(
            turn=dict(TURN), store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None, provider_factory=lambda: child, workspace_root=tmp_path, child_provider_factory=lambda _model: child,
            lifecycle=bus,
        )
        session.events = []
        bus.subscribe(session.events.append)
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close_background_subagents()
    engine.dispose()


def _wait(session: TurnSession, agent_id: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        entry = session._background_subagents.get(agent_id)
        if entry is not None and entry["future"].done():
            return
        time.sleep(0.05)
    raise AssertionError("the teammate did not finish")


def test_a_named_teammate_runs_in_the_background_and_goes_idle(team) -> None:
    session = team(_Child())
    started = session._run_subagent(subagent_type="general-purpose", task="Look at the parser", teammate_name="alice", team_name="core")
    assert started.status == "succeeded", started.content
    assert started.payload["background"] is True and started.payload["agent_name"] == "alice"
    _wait(session, started.payload["agent_id"])
    idle = [dict(event.payload) for event in session.events if event.name is HookEvent.TEAMMATE_IDLE]
    assert idle and idle[0]["teammate_name"] == "alice" and idle[0]["team_name"] == "core"
    assert any("Teammate alice is idle" in item.text for item in session._reminders.drain())


def test_a_message_to_an_idle_teammate_starts_it_with_the_message(team) -> None:
    child = _Child()
    session = team(child)
    started = session._run_subagent(subagent_type="general-purpose", task="Look at the parser", teammate_name="alice", team_name="core")
    _wait(session, started.payload["agent_id"])
    sent = session._send_team_message(sender="lead", to="alice", message="Now check the lexer too", summary="lexer")
    assert sent.status == "succeeded" and sent.payload["status"] == "running"
    _wait(session, started.payload["agent_id"])
    assert "Now check the lexer too" in child.requests[-1] and "From lead (lexer)" in child.requests[-1]


def test_a_message_to_a_busy_teammate_waits_for_its_current_task(team) -> None:
    child = _Child(hold=True)
    session = team(child)
    started = session._run_subagent(subagent_type="general-purpose", task="Long task", teammate_name="bob", team_name="core")
    assert child.started.wait(10)
    queued = session._send_team_message(sender="lead", to="bob", message="Also update the docs")
    assert queued.status == "succeeded" and queued.payload["status"] == "queued"
    child.release.set()
    _wait(session, started.payload["agent_id"])
    assert len(child.requests) >= 2 and "Also update the docs" in child.requests[-1]


def test_a_teammate_writes_to_the_lead(team) -> None:
    session = team(_Child(message_lead=True))
    started = session._run_subagent(subagent_type="general-purpose", task="Find the bug", teammate_name="carol", team_name="core")
    _wait(session, started.payload["agent_id"])
    texts = [item.text for item in session._reminders.drain() if item.kind == "teammate"]
    assert any("Message from teammate carol" in text and "Found the bug in parser.py" in text for text in texts)
    history = session._team_mailbox().history(user_id=TURN["user_id"], conversation_id=TURN["conversation_id"])
    assert [(item["sender"], item["recipient"]) for item in history] == [("carol", "lead")]


def test_refusals(team) -> None:
    session = team(_Child())
    assert session._send_team_message(sender="lead", to="nobody", message="hi").error_code == "AGENT_NOT_FOUND"
    assert session._send_team_message(sender="lead", to="lead", message="hi").status == "failed"
    started = session._run_subagent(subagent_type="general-purpose", task="x", teammate_name="dave", team_name="core")
    _wait(session, started.payload["agent_id"])
    assert session._send_team_message(sender="lead", to="dave", message="   ").status == "failed"
    assert session._send_team_message(sender="dave", to="dave", message="hi").status == "failed"
    for bad in ("Lead", "has space", "x" * 40):
        assert session._run_subagent(subagent_type="general-purpose", task="x", teammate_name=bad).status == "failed"


def test_send_message_is_offered_only_where_a_team_can_exist(tmp_path: Path) -> None:
    plain = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True)
    assert "send_message" not in {item.name for item in plain.definitions()}
    assert plain.invoke("SendMessage", {"to": "alice", "message": "hi"}).error_code == "UNKNOWN_TOOL"
    seen: list[dict] = []
    lead = AgentToolset(ConversationWorkspace(tmp_path / "w2", "c1"), public_tool_names=True)
    lead._team_sender = "lead"
    lead._send_team_message = lambda **kwargs: seen.append(kwargs) or {"summary": "ok", "content": "sent"}
    assert lead.invoke("SendMessage", {"to": "alice", "message": "hi", "summary": "s"}).status == "succeeded"
    assert seen == [{"sender": "lead", "to": "alice", "message": "hi", "summary": "s"}]


def test_the_agent_tool_passes_a_teammate_name_through(tmp_path: Path) -> None:
    from agentos.agentic.tools._shared import ToolOutcome

    calls: list[dict] = []

    def run_subagent(subagent_type, task, **kwargs):
        calls.append(kwargs)
        return ToolOutcome("succeeded", "ok", "started", {})

    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), run_subagent=run_subagent, public_tool_names=True)
    toolset.invoke("Agent", {"subagent_type": "general-purpose", "prompt": "x", "description": "d", "name": "alice", "team_name": "core"})
    assert calls[-1]["teammate_name"] == "alice" and calls[-1]["team_name"] == "core"

def _pending(session: TurnSession, name: str) -> int:
    return session._team_mailbox().pending_count(user_id=TURN["user_id"], conversation_id=TURN["conversation_id"], recipient=name)


def test_a_message_survives_a_teammate_that_cannot_start(team) -> None:
    child = _Child()
    session = team(child)
    started = session._run_subagent(subagent_type="general-purpose", task="First task", teammate_name="erin", team_name="core")
    _wait(session, started.payload["agent_id"])
    session._max_subagents_per_turn = session._subagent_runs  # the turn's subagent budget is used up
    refused = session._send_team_message(sender="lead", to="erin", message="Check the cache too")
    assert refused.status == "failed" and refused.error_code == "SUBAGENT_LIMIT"
    assert "kept" in refused.content and _pending(session, "erin") == 1
    session._max_subagents_per_turn = None
    resumed = session._send_team_message(sender="lead", to="erin", message="And the logs")
    assert resumed.payload["status"] == "running"
    _wait(session, started.payload["agent_id"])
    assert "Check the cache too" in child.requests[-1] and "And the logs" in child.requests[-1]


def test_a_message_sent_while_a_teammate_goes_idle_starts_it_again(team) -> None:
    child = _Child()
    session = team(child)
    entered, release = threading.Event(), threading.Event()
    original = session._teammate_idle

    def slow_idle(*args, **kwargs):
        entered.set()
        release.wait(10)
        return original(*args, **kwargs)

    session._teammate_idle = slow_idle
    started = session._run_subagent(subagent_type="general-purpose", task="First task", teammate_name="frank", team_name="core")
    assert entered.wait(10)  # its run ended and it is announcing that it is idle
    sent = session._send_team_message(sender="lead", to="frank", message="One more thing")
    assert sent.status == "succeeded" and sent.payload["status"] == "running", sent.content
    release.set()
    _wait(session, started.payload["agent_id"])
    assert "One more thing" in child.requests[-1]


def test_after_the_delivery_cap_the_lead_hears_of_unread_messages(team, monkeypatch) -> None:
    import agentos.agentic.session as session_module

    monkeypatch.setattr(session_module, "MAX_TEAM_DELIVERIES", 0)
    child = _Child(hold=True)
    session = team(child)
    started = session._run_subagent(subagent_type="general-purpose", task="Long task", teammate_name="gina", team_name="core")
    assert child.started.wait(10)
    assert session._send_team_message(sender="lead", to="gina", message="Later").payload["status"] == "queued"
    child.release.set()
    _wait(session, started.payload["agent_id"])
    texts = [item.text for item in session._reminders.drain() if item.kind == "teammate"]
    assert any("Teammate gina is idle" in text and "1 unread message" in text for text in texts)
    assert _pending(session, "gina") == 1


def test_a_teammate_keeps_its_team(team) -> None:
    session = team(_Child())
    started = session._run_subagent(subagent_type="general-purpose", task="x", teammate_name="hank", team_name="core")
    _wait(session, started.payload["agent_id"])
    moved = session._run_subagent(subagent_type="general-purpose", task="y", teammate_name="hank", team_name="other")
    assert moved.status == "failed" and moved.error_code == "INVALID_ARGUMENTS" and "core" in moved.content
    again = session._run_subagent(subagent_type="general-purpose", task="y", teammate_name="hank")
    assert again.status == "succeeded", again.content
    _wait(session, started.payload["agent_id"])
    assert session._team_roster()["hank"]["team_name"] == "core"


def test_a_stopped_teammate_leaves_its_mail_unread(team) -> None:
    child = _Child(hold=True)
    session = team(child)
    started = session._run_subagent(subagent_type="general-purpose", task="Long task", teammate_name="iris", team_name="core")
    assert child.started.wait(10)
    session._send_team_message(sender="lead", to="iris", message="Also this")
    session._stop_background_subagent(started.payload["agent_id"])
    child.release.set()
    _wait(session, started.payload["agent_id"])
    assert len(child.requests) == 1 and _pending(session, "iris") == 1

def test_a_teammate_is_released_when_starting_it_raises(team) -> None:
    session = team(_Child())
    started = session._run_subagent(subagent_type="general-purpose", task="x", teammate_name="jack", team_name="core")
    _wait(session, started.payload["agent_id"])

    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    session._ask_agent = broken
    with pytest.raises(RuntimeError):
        session._send_team_message(sender="lead", to="jack", message="Keep this")
    assert session._team_roster()["jack"]["busy"] is False and _pending(session, "jack") == 1
    session._start_subagent = broken
    with pytest.raises(RuntimeError):
        session._run_subagent(subagent_type="general-purpose", task="y", teammate_name="jack")
    assert session._team_roster()["jack"]["busy"] is False
