"""WS-B7 Criterion 11: CLI subagent ending without tool calls continues on unread messages (Claude CLI send_user & Codex resume)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import _AgentControlWatcher
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.agent_controls import InMemoryAgentControls
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.stream import CliUsage
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
)


@dataclass(frozen=True)
class _Caps:
    persistent_stdin: bool
    supports_session_resume: bool


class _SubagentFakeStore(FakeStore):
    def __init__(self, *, model_id: str = "claude-opus-5-5", provider: str = "anthropic") -> None:
        super().__init__(model_id=model_id)
        self.turn["provider"] = provider
        self.turn["is_subagent"] = True
        self.agent_id = "agent:c1:sub-1"
        self.text = ""


def _make_watcher(
    controls: InMemoryAgentControls,
    main_reminders: ReminderQueue,
    events: list[tuple[AgentActivityEventType, dict[str, Any]]],
    *,
    agent_id: str = "agent:c1:sub-1",
    stop_event: threading.Event | None = None,
) -> _AgentControlWatcher:
    return _AgentControlWatcher(
        store=controls,
        user_id="u1",
        conversation_id="c1",
        agent_id=agent_id,
        clean="worker",
        event=stop_event if stop_event is not None else threading.Event(),
        reminders=ReminderQueue(),
        main_reminders=main_reminders,
        record_activity=lambda et, _summary, payload, **_kw: events.append((et, dict(payload or {}))),
        clock=lambda: 100.0,
        sender_name_resolver=lambda _aid: "Orin",
    )


def test_claude_cli_subagent_continues_via_send_user_on_unread_message(tmp_path: Path) -> None:
    """Fake Claude CLI (persistent_stdin=True): ends without tool call with unread message -> send_user + delivered."""
    controls = InMemoryAgentControls()
    main_reminders = ReminderQueue()
    events: list[tuple[AgentActivityEventType, dict[str, Any]]] = []
    watcher = _make_watcher(controls, main_reminders, events)
    store = _SubagentFakeStore()
    posted: list[str] = []

    def _round1(req: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        # Message arrives right before round 1 ends (without any tool call in round 1).
        cid = controls.post_message("u1", "c1", "agent:c1:sub-1", "inclua tambem o arquivo Z", sender_kind="parent", sender_agent_id="main")
        posted.append(cid)
        sink.on_text("Primeira resposta.")
        sink.on_assistant_usage("m1", CliUsage(input_tokens=50, output_tokens=10))
        return NativeTurnOutcome(result=_make_result(text="Primeira resposta."), init=init, final_text="Primeira resposta.", exit_code=0)

    def _round2(req: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle) -> NativeTurnOutcome:
        sink.on_text("Atualizado com arquivo Z.")
        sink.on_assistant_usage("m2", CliUsage(input_tokens=60, output_tokens=15))
        return NativeTurnOutcome(result=_make_result(text="Atualizado com arquivo Z."), init=None, final_text="Atualizado com arquivo Z.", exit_code=0)

    provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round1, _round2]))
    provider.adapter = SimpleNamespace(name="claude", capabilities=_Caps(persistent_stdin=True, supports_session_resume=False))

    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), public_tool_names=True)
    rt = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_actions=5, max_turns=5),
        reminders=watcher.reminders,
        on_reminder_ack=watcher.ack_control,
        control_watcher=watcher,
        continue_on_unread=True,
    )
    res = rt.run("t1")
    watcher.finalize()

    assert res.state == "completed"
    assert provider.starts == 1
    assert len(provider.handles) == 1
    assert len(provider.handles[0].sent_users) == 1
    sent_text = provider.handles[0].sent_users[0][0]["text"]
    assert "inclua tambem o arquivo Z" in sent_text
    assert "Message from parent agent Orin" in sent_text

    listed = controls.list_for_agent("u1", "c1", "agent:c1:sub-1")
    assert listed[0]["control_id"] == posted[0]
    assert listed[0]["state"] == "delivered"
    assert listed[0]["delivered_at"] is not None
    assert main_reminders.drain() == []
    assert any(et == AgentActivityEventType.AGENT_MESSAGE_DELIVERED for et, _ in events)


def test_codex_cli_subagent_resumes_session_on_unread_message(tmp_path: Path) -> None:
    """Fake Codex CLI (persistent_stdin=False, supports_session_resume=True): resumes session with unread message."""
    controls = InMemoryAgentControls()
    main_reminders = ReminderQueue()
    events: list[tuple[AgentActivityEventType, dict[str, Any]]] = []
    watcher = _make_watcher(controls, main_reminders, events)
    store = _SubagentFakeStore(model_id="gpt-6.1-sol", provider="openai-codex")
    posted: list[str] = []
    call_idx = [0]

    def _factory(req: NativeTurnRequest) -> ScriptedHandle:
        call_idx[0] += 1
        if call_idx[0] == 1:
            def _r1(r: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle) -> NativeTurnOutcome:
                init = _make_init(r.model)
                sink.on_init(init)
                cid = controls.post_message("u1", "c1", "agent:c1:sub-1", "confira tambem os testes", sender_kind="user")
                posted.append(cid)
                sink.on_text("R1")
                sink.on_assistant_usage("m1", CliUsage(input_tokens=40, output_tokens=10))
                return NativeTurnOutcome(result=_make_result(text="R1"), init=init, final_text="R1", exit_code=0)
            return ScriptedHandle(req, [_r1])

        def _r2(r: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle) -> NativeTurnOutcome:
            sink.on_text("R2 com testes")
            sink.on_assistant_usage("m2", CliUsage(input_tokens=45, output_tokens=12))
            return NativeTurnOutcome(result=_make_result(text="R2 com testes"), init=None, final_text="R2 com testes", exit_code=0)
        return ScriptedHandle(req, [_r2])

    provider = FakeNativeProvider(handle_factory=_factory)
    provider.adapter = SimpleNamespace(name="codex", capabilities=_Caps(persistent_stdin=False, supports_session_resume=True))

    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), public_tool_names=True)
    rt = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_actions=5, max_turns=5),
        reminders=watcher.reminders,
        on_reminder_ack=watcher.ack_control,
        control_watcher=watcher,
        continue_on_unread=True,
    )
    res = rt.run("t1")
    watcher.finalize()

    assert res.state == "completed"
    assert provider.starts == 2
    assert provider.requests[1].resume_session_id == "sess_1"
    resumed_text = provider.requests[1].user_content[0]["text"]
    assert "confira tambem os testes" in resumed_text

    listed = controls.list_for_agent("u1", "c1", "agent:c1:sub-1")
    assert listed[0]["control_id"] == posted[0]
    assert listed[0]["state"] == "delivered"
    assert listed[0]["delivered_at"] is not None
    assert main_reminders.drain() == []


def test_action_budget_exhausted_forwards_unread_message_to_parent(tmp_path: Path) -> None:
    """When action budget is exhausted (max_actions=0), continuation is refused and finalize forwards to parent."""
    controls = InMemoryAgentControls()
    main_reminders = ReminderQueue()
    events: list[tuple[AgentActivityEventType, dict[str, Any]]] = []
    watcher = _make_watcher(controls, main_reminders, events)
    store = _SubagentFakeStore()

    def _round1(req: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        controls.post_message("u1", "c1", "agent:c1:sub-1", "orcamento esgotado", sender_kind="parent", sender_agent_id="main")
        sink.on_text("Fim sem orcamento.")
        sink.on_assistant_usage("m1", CliUsage(input_tokens=50, output_tokens=10))
        return NativeTurnOutcome(result=_make_result(text="Fim sem orcamento."), init=init, final_text="Fim sem orcamento.", exit_code=0)

    provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round1]))
    provider.adapter = SimpleNamespace(name="claude", capabilities=_Caps(persistent_stdin=True, supports_session_resume=False))

    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), public_tool_names=True)
    rt = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_actions=0, max_turns=5),
        reminders=watcher.reminders,
        on_reminder_ack=watcher.ack_control,
        control_watcher=watcher,
        continue_on_unread=True,
    )
    res = rt.run("t1")
    watcher.finalize()

    assert res.state == "completed"
    assert provider.handles[0].sent_users == []
    listed = controls.list_for_agent("u1", "c1", "agent:c1:sub-1")
    assert listed[0]["state"] == "forwarded"
    assert listed[0]["delivered_at"] is None
    assert any("orcamento esgotado" in r.text for r in main_reminders.drain())


def test_continue_on_unread_false_forwards_to_parent(tmp_path: Path) -> None:
    """When orchestration.agentMessages.continueOnUnread is False, unread messages forward to parent as before."""
    controls = InMemoryAgentControls()
    main_reminders = ReminderQueue()
    events: list[tuple[AgentActivityEventType, dict[str, Any]]] = []
    watcher = _make_watcher(controls, main_reminders, events)
    store = _SubagentFakeStore()

    def _round1(req: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        controls.post_message("u1", "c1", "agent:c1:sub-1", "nao continuar", sender_kind="user")
        sink.on_text("Fim.")
        sink.on_assistant_usage("m1", CliUsage(input_tokens=50, output_tokens=10))
        return NativeTurnOutcome(result=_make_result(text="Fim."), init=init, final_text="Fim.", exit_code=0)

    provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round1]))
    provider.adapter = SimpleNamespace(name="claude", capabilities=_Caps(persistent_stdin=True, supports_session_resume=False))

    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), public_tool_names=True)
    rt = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_actions=5, max_turns=5),
        reminders=watcher.reminders,
        on_reminder_ack=watcher.ack_control,
        control_watcher=watcher,
        continue_on_unread=False,
    )
    res = rt.run("t1")
    watcher.finalize()

    assert res.state == "completed"
    assert provider.handles[0].sent_users == []
    listed = controls.list_for_agent("u1", "c1", "agent:c1:sub-1")
    assert listed[0]["state"] == "forwarded"
    assert any("nao continuar" in r.text for r in main_reminders.drain())
