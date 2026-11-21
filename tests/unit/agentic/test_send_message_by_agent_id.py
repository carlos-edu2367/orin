"""WS-B7: SendMessage(agent_id=...) targeting running subagents, subtree security, validation & parentTool setting."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentos.agentic.agent_messaging import AgentMessenger
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession
from agentos.agentic.tools.agents.team import SendMessageInput
from agentos.agentic.tools.public_names import PublicToolNames
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.agent_controls import InMemoryAgentControls
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.loader import EffectiveSettings, SettingsLayer
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _parallel_tool_calls_batch,
    _text_batch,
)


class _ActivityStore(_RecordingStore):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[AgentActivityEventType, str, str | None, dict[str, Any]]] = []

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        super().record(turn, event_type, summary, payload, agent_id=agent_id, parent_agent_id=parent_agent_id)
        with self._lock:
            self.events.append((event_type, str(summary), agent_id, dict(payload or {})))


def test_send_message_input_requires_exactly_one_of_to_or_agent_id() -> None:
    assert SendMessageInput(to="alice", message="oi").to == "alice"
    assert SendMessageInput(agent_id="agent-1", message="oi").agent_id == "agent-1"

    with pytest.raises(ValidationError):
        SendMessageInput(message="oi")

    with pytest.raises(ValidationError):
        SendMessageInput(to="alice", agent_id="agent-1", message="oi")


def test_agent_messenger_subtree_security_self_and_not_running() -> None:
    controls = InMemoryAgentControls()
    events: list[tuple[str, dict[str, Any]]] = []
    tree = {
        "child-a": "main",
        "child-b": "main",
        "grandchild-a1": "child-a",
    }
    running = {"child-a", "child-b", "grandchild-a1"}

    messenger = AgentMessenger(
        controls=controls,
        user_id="u1",
        conversation_id="conv-1",
        main_agent_id="main",
        agent_tree=lambda: dict(tree),
        is_running=lambda aid: aid in running,
        emit_event=lambda et, payload: events.append((et, dict(payload))),
    )

    # Self-message refused without writing.
    self_res = messenger.send("child-a", "child-a", "ei")
    assert self_res.status == "failed" and self_res.error_code == "INVALID_ARGUMENTS"

    # Security: sibling cannot message sibling.
    sib_res = messenger.send("child-a", "child-b", "faça outra coisa")
    assert sib_res.status == "failed" and sib_res.error_code == "INVALID_ARGUMENTS"

    # Security: child cannot message parent or main.
    up_res = messenger.send("grandchild-a1", "child-a", "mude")
    assert up_res.status == "failed" and up_res.error_code == "INVALID_ARGUMENTS"
    up_main = messenger.send("child-a", "main", "mude")
    assert up_main.status == "failed" and up_main.error_code == "INVALID_ARGUMENTS"

    # Unknown agent -> INVALID_ARGUMENTS.
    unk_res = messenger.send("main", "unknown-id", "oi")
    assert unk_res.status == "failed" and unk_res.error_code == "INVALID_ARGUMENTS"

    # Nothing written to store yet.
    assert controls.list_for_agent("u1", "conv-1", "child-a") == []
    assert controls.list_for_agent("u1", "conv-1", "child-b") == []
    assert controls.list_for_agent("u1", "conv-1", "grandchild-a1") == []

    # Finished agent -> AGENT_NOT_RUNNING without writing.
    running.discard("child-b")
    stopped_res = messenger.send("main", "child-b", "tarde demais")
    assert stopped_res.status == "failed" and stopped_res.error_code == "AGENT_NOT_RUNNING"
    assert controls.list_for_agent("u1", "conv-1", "child-b") == []

    # Main -> descendant (including grandchild) succeeds.
    ok_main = messenger.send("main", "grandchild-a1", "pare de editar X", summary="foco")
    assert ok_main.status == "succeeded"
    assert ok_main.payload["status"] == "queued"
    assert ok_main.payload["agent_id"] == "grandchild-a1"
    assert ok_main.payload["control_id"]

    # Subagent -> its own descendant succeeds.
    ok_sub = messenger.send("child-a", "grandchild-a1", "ajuste o teste")
    assert ok_sub.status == "succeeded"
    assert ok_sub.payload["status"] == "queued"

    stored = controls.list_for_agent("u1", "conv-1", "grandchild-a1")
    assert len(stored) == 2
    assert stored[0]["sender_kind"] == "parent"
    assert stored[0]["sender_agent_id"] == "main"
    assert stored[1]["sender_kind"] == "parent"
    assert stored[1]["sender_agent_id"] == "child-a"
    assert [ev[0] for ev in events] == ["agent.message_queued", "agent.message_queued"]
    assert events[0][1]["control_id"] == ok_main.payload["control_id"]
    assert events[0][1]["agent_id"] == "grandchild-a1"
    assert events[0][1]["sender_kind"] == "parent"


def test_send_message_availability_and_parent_tool_setting(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "w", "c1")
    has_team = False
    has_children = False

    ts = AgentToolset(ws, public_tool_names=True)
    ts._team_sender = "lead"
    ts._send_team_message = lambda **kwargs: {"summary": "ok", "content": "ok"}
    ts._send_agent_message = lambda **kwargs: {"summary": "ok", "content": "queued"}
    ts._has_team = lambda: has_team
    ts._has_living_children = lambda: has_children
    ts._parent_message_tool_enabled = True

    # Neither team nor living children -> not published (no tool bloat).
    assert "send_message" not in {d.name for d in ts.definitions()}

    # Living child appears -> SendMessage is published with agent_id.
    has_children = True
    ts.refresh_definitions()
    assert "send_message" in {d.name for d in ts.definitions()}
    pub_schemas = PublicToolNames().schemas_out(ts.schemas())
    sm_schema = next(s for s in pub_schemas if s["function"]["name"] == "SendMessage")
    assert "agent_id" in sm_schema["function"]["parameters"]["properties"]

    # Both or neither target -> INVALID_ARGUMENTS.
    both = ts.invoke("SendMessage", {"to": "alice", "agent_id": "a1", "message": "oi"})
    assert both.status == "failed" and both.error_code == "INVALID_ARGUMENTS"
    neither = ts.invoke("SendMessage", {"message": "oi"})
    assert neither.status == "failed" and neither.error_code == "INVALID_ARGUMENTS"

    # parentTool=False with no team -> SendMessage is NOT published.
    ts._parent_message_tool_enabled = False
    ts.refresh_definitions()
    assert "send_message" not in {d.name for d in ts.definitions()}

    # parentTool=False WITH a team -> SendMessage published WITHOUT agent_id in schema!
    has_team = True
    ts.refresh_definitions()
    assert "send_message" in {d.name for d in ts.definitions()}
    pub_schemas_no_parent = PublicToolNames().schemas_out(ts.schemas())
    sm_no_parent = next(s for s in pub_schemas_no_parent if s["function"]["name"] == "SendMessage")
    assert "agent_id" not in sm_no_parent["function"]["parameters"]["properties"]
    refused = ts.invoke("SendMessage", {"agent_id": "a1", "message": "oi"})
    assert refused.status == "failed"


def test_parent_sends_message_to_running_background_subagent_and_gets_real_ack(tmp_path: Path) -> None:
    """Criterion 9: main sends SendMessage(agent_id=...) to running background child, delivered on next tool round."""
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'b7_c9.db').as_posix()}")
    metadata.create_all(engine)
    store = _ActivityStore()
    agents_store = ConversationAgentStore(
        engine,
        conversation_id=TURN["conversation_id"],
        user_id=TURN["user_id"],
    )

    tool_started = threading.Event()
    allow_tool_finish = threading.Event()
    second_request_seen = threading.Event()
    state_before_drain: list[str] = []

    class _BgChildProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def stream(self, request: dict[str, Any]):
            self.requests.append(request)
            if len(self.requests) == 1:
                return iter(_parallel_tool_calls_batch([("c1", "list_files", {"path": "."})]))
            second_request_seen.set()
            return iter(_text_batch("Parei de editar X conforme orientado."))

    child_provider = _BgChildProvider()
    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: child_provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: child_provider,
    )
    now = [100.0]
    session._agent_control_clock = lambda: now[0]

    # Hook list_files so we can pause mid-tool, send the parent message, poll, inspect `injected`, then resume.
    orig_toolset = session._toolset

    def _patched_toolset(*args, **kwargs):
        ts = orig_toolset(*args, **kwargs)
        if not kwargs.get("subagents", True) or kwargs.get("caller") is not None:
            orig_list = ts.list_files

            def _slow_list(path=".", depth=1, pattern=None):
                tool_started.set()
                allow_tool_finish.wait(10)
                return orig_list(path=path, depth=depth, pattern=pattern)

            ts.list_files = _slow_list
        return ts

    session._toolset = _patched_toolset  # type: ignore[assignment]

    try:
        started = session._run_subagent("general", "Edite X e Y", run_in_background=True)
        assert started.status == "succeeded"
        child_id = str(started.payload["agent_id"])

        assert tool_started.wait(10), "background child never started its tool call"

        # Send message from main agent to running background child via AgentMessenger / SendMessage.
        sent = session._send_agent_message(agent_id=child_id, message="pare de editar X")
        assert sent.status == "succeeded", sent.content
        assert sent.payload["status"] == "queued"
        control_id = str(sent.payload["control_id"])

        # Trigger watcher poll while tool is still executing -> must be `injected`, NOT `delivered` yet!
        now[0] += 2.0
        watcher = session._active_watchers[child_id]
        watcher.poll()
        listed_before = session._agent_controls().list_for_agent(
            TURN["user_id"], TURN["conversation_id"], child_id
        )
        assert len(listed_before) == 1
        state_before_drain.append(str(listed_before[0]["state"]))
        assert listed_before[0]["state"] == "injected"
        assert listed_before[0]["delivered_at"] is None

        # Let tool finish -> next model request drains reminders -> acks delivered!
        allow_tool_finish.set()
        assert second_request_seen.wait(10)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            entry = session._background_subagents.get(child_id)
            if entry is not None and entry["future"].done():
                break
            time.sleep(0.05)

        assert len(child_provider.requests) == 2
        req2_repr = repr(child_provider.requests[1])
        assert "pare de editar X" in req2_repr
        assert "Message from parent agent" in req2_repr

        listed_after = session._agent_controls().list_for_agent(
            TURN["user_id"], TURN["conversation_id"], child_id
        )
        assert listed_after[0]["state"] == "delivered"
        assert listed_after[0]["delivered_at"] is not None

        event_types = [ev[0] for ev in store.events]
        assert AgentActivityEventType.AGENT_MESSAGE_QUEUED in event_types
        assert AgentActivityEventType.AGENT_MESSAGE_DELIVERED in event_types
        queued_payload = next(ev[3] for ev in store.events if ev[0] == AgentActivityEventType.AGENT_MESSAGE_QUEUED)
        delivered_payload = next(ev[3] for ev in store.events if ev[0] == AgentActivityEventType.AGENT_MESSAGE_DELIVERED)
        assert queued_payload["control_id"] == control_id
        assert queued_payload["agent_id"] == child_id
        assert queued_payload["sender_kind"] == "parent"
        assert delivered_payload["control_id"] == control_id
        assert delivered_payload["agent_id"] == child_id
        assert delivered_payload["sender_kind"] == "parent"
    finally:
        allow_tool_finish.set()
        session.close_background_subagents()
        engine.dispose()


def test_session_respects_orchestration_parent_tool_false(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'b7_setting.db').as_posix()}")
    metadata.create_all(engine)
    try:
        session = TurnSession(
            turn=dict(TURN),
            store=_ActivityStore(),
            agents_store=ConversationAgentStore(
                engine,
                conversation_id=TURN["conversation_id"],
                user_id=TURN["user_id"],
            ),
            memory_store=None,
            provider_factory=lambda: None,
            workspace_root=tmp_path,
        )
        session.effective_settings = EffectiveSettings(
            data={"orchestration": {"agentMessages": {"parentTool": False}}},
            provenance={"orchestration.agentMessages.parentTool": "project"},
            errors=(),
            trusted=True,
            layers=(
                SettingsLayer(
                    name="project",
                    path=None,
                    data={"orchestration": {"agentMessages": {"parentTool": False}}},
                ),
            ),
        )
        assert session._parent_message_tool_enabled() is False
    finally:
        engine.dispose()
