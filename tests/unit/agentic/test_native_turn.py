from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import time
from typing import Any, Callable, Mapping

import pytest

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.native_turn import (
    BATCH_GATHER_SECONDS,
    NativeTurnRunner,
    RuntimeToolExecutor,
    TOOL_USE_ID_META_KEY,
    ToolBatchCollector,
)
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.errors import (
    NativeLoopUnavailable,
    ProviderTransportError,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage
from agentos.providers.cli_tools_mcp import ToolCallRequest, ToolCallResult, TurnToolServer


def _make_init(model: str = "claude-opus-5-5") -> CliInit:
    return CliInit(
        session_id="sess_1",
        model=model,
        api_key_source="none",
        mcp_servers={"orin": "connected"},
        tools=("mcp__orin__Read", "mcp__orin__Edit"),
    )


def _make_result(
    *,
    subtype: str = "success",
    is_error: bool = False,
    text: str = "Done.",
    usage: CliUsage | None = None,
) -> CliResult:
    u = usage or CliUsage(input_tokens=100, output_tokens=20)
    return CliResult(
        subtype=subtype,
        is_error=is_error,
        result_text=text,
        stop_reason="end_turn",
        num_turns=1,
        duration_ms=50,
        session_id="sess_1",
        api_error_status=None,
        usage=u,
        model_usage={"claude-opus-5-5": u},
    )


class FakeStore:
    def __init__(self, *, model_id: str = "claude-opus-5-5") -> None:
        self.turn: dict[str, object] = {
            "turn_id": "t1",
            "conversation_id": "c1",
            "user_id": "u1",
            "execution_id": "e1",
            "provider": "anthropic",
            "model_id": model_id,
            "user_message_id": "um1",
            "assistant_message_id": "am1",
            "code_mode": "code",
            "conversation_mode": "code",
        }
        self.states: list[tuple[str, dict[str, Any]]] = []
        self.deltas: list[str] = []
        self.effects_started: list[dict[str, Any]] = []
        self.effects_finished: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.usages: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self._eff_seq = 0

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        return [{"role": "user", "content": "Inspect and fix the project"}]

    def lifecycle(self, turn: Mapping[str, object], state: str, **payload: object) -> None:
        self.states.append((state, dict(payload)))

    def delta(self, turn: Mapping[str, object], text: str) -> None:
        self.deltas.append(text)

    def effect_started(
        self,
        turn: Mapping[str, object],
        *,
        kind: str,
        invocation_ref: str,
        request_ref: str,
    ) -> str:
        self._eff_seq += 1
        eff_id = f"eff_{self._eff_seq}"
        self.effects_started.append(
            {
                "effect_id": eff_id,
                "kind": kind,
                "invocation_ref": invocation_ref,
                "request_ref": request_ref,
            }
        )
        return eff_id

    def effect_finished(
        self,
        turn: Mapping[str, object],
        *,
        effect_id: str,
        state: str,
        result_ref: str | None = None,
        error_code: str | None = None,
        private_result: Mapping[str, object] | None = None,
    ) -> None:
        self.effects_finished.append(
            {
                "effect_id": effect_id,
                "state": state,
                "result_ref": result_ref,
                "error_code": error_code,
                "private_result": dict(private_result or {}),
            }
        )

    def record_step(
        self,
        turn: Mapping[str, object],
        *,
        kind: str,
        payload: Mapping[str, object],
        **fields: object,
    ) -> None:
        self.steps.append({"kind": kind, "payload": dict(payload), **fields})

    def record_usage(
        self,
        turn: Mapping[str, object],
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self.usages.append(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        )

    def finish(
        self,
        turn: Mapping[str, object],
        *,
        failed: bool = False,
        code: str | None = None,
    ) -> None:
        self.finished.append({"failed": failed, "code": code})


class ScriptedHandle:
    def __init__(
        self,
        req: NativeTurnRequest,
        rounds: list[Callable[[NativeTurnRequest, NativeTurnSink, "ScriptedHandle"], NativeTurnOutcome]],
    ) -> None:
        self.req = req
        self._rounds = list(rounds)
        self.sent_users: list[list[dict[str, Any]]] = []
        self.refused_reasons: list[str] = []
        self.closed = False

    def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
        if not self._rounds:
            raise AssertionError("No more scripted rounds in ScriptedHandle")
        fn = self._rounds.pop(0)
        return fn(self.req, sink, self)

    def send_user(self, content: list[dict[str, Any]]) -> None:
        self.sent_users.append(list(content))

    def refuse_tools(self, reason: str = "turn ended") -> None:
        self.refused_reasons.append(reason)

    def close(self) -> None:
        self.closed = True


class FakeNativeProvider:
    def __init__(
        self,
        *,
        available: bool = True,
        raise_on_start: BaseException | None = None,
        handle_factory: Callable[[NativeTurnRequest], ScriptedHandle] | None = None,
    ) -> None:
        self.available = available
        self.raise_on_start = raise_on_start
        self._handle_factory = handle_factory
        self.starts = 0
        self.stream_calls = 0
        self.requests: list[NativeTurnRequest] = []
        self.handles: list[ScriptedHandle] = []

    def native_turn_available(self, turn: Mapping[str, object]) -> bool:
        return self.available

    def start_native_turn(self, req: NativeTurnRequest) -> ScriptedHandle:
        self.starts += 1
        self.requests.append(req)
        if self.raise_on_start is not None:
            err = self.raise_on_start
            self.raise_on_start = None
            raise err
        if self._handle_factory is not None:
            handle = self._handle_factory(req)
        else:
            def _default_round(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
                init = _make_init(r.model)
                sink.on_init(init)
                sink.on_text("Hello from native turn.")
                sink.on_assistant_usage("msg_1", CliUsage(input_tokens=100, output_tokens=20))
                return NativeTurnOutcome(
                    result=_make_result(text="Hello from native turn."),
                    init=init,
                    final_text="Hello from native turn.",
                    exit_code=0,
                )

            handle = ScriptedHandle(req, [_default_round])
        self.handles.append(handle)
        return handle

    def stream(self, request: Mapping[str, object]):
        self.stream_calls += 1
        return iter(
            [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Fallback text answer."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]
        )


def _make_toolset(workspace: ConversationWorkspace, **kwargs: Any) -> AgentToolset:
    kwargs.setdefault("code_mode_active", True)
    kwargs.setdefault("public_tool_names", True)
    kwargs.setdefault("code_mode_work_kind", "analysis")
    return AgentToolset(workspace, **kwargs)


def test_runtime_hands_whole_turn_to_native_provider_once(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    fake_provider = FakeNativeProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    result = runtime.run("t1", turn=store.turn)
    assert result.state == "completed" and fake_provider.starts == 1 and fake_provider.stream_calls == 0
    assert fake_provider.handles[0].closed is True


def test_mcp_call_goes_through_run_toolset_with_permissions_hooks_and_effects(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "f.py").write_text("x = 1\n", encoding="utf-8")

    bus = LifecycleBus(base={"session_id": "c1"})
    bus.subscribe(
        lambda ev: LifecycleDecision(
            blocked=True,
            permission_decision="deny",
            reason="bloqueado pelo teste",
        )
        if ev.name is HookEvent.PRE_TOOL_USE and ev.payload.get("tool_name") == "Edit"
        else None
    )
    toolset = _make_toolset(workspace, lifecycle=bus)

    captured_results: list[ToolCallResult] = []

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_tool_use_started("toolu_edit_1", "mcp__orin__Edit")
        sink.on_tool_use_complete(
            "toolu_edit_1",
            "mcp__orin__Edit",
            {"file_path": "f.py", "old_string": "x = 1", "new_string": "x = 2"},
        )
        res = req.executor.call_tool(
            ToolCallRequest(
                name="Edit",
                arguments={"file_path": "f.py", "old_string": "x = 1", "new_string": "x = 2"},
                meta={TOOL_USE_ID_META_KEY: "toolu_edit_1"},
            )
        )
        captured_results.append(res)
        sink.on_text("Edit was blocked.")
        return NativeTurnOutcome(
            result=_make_result(text="Edit was blocked."),
            init=init,
            final_text="Edit was blocked.",
            exit_code=0,
        )

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        lifecycle=bus,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"
    assert len(captured_results) == 1
    assert captured_results[0].is_error is True
    assert "Um hook PreToolUse bloqueou" in captured_results[0].text

    tool_effects = [e for e in store.effects_started if e["kind"] == "tool"]
    assert len(tool_effects) == 1
    assert tool_effects[0]["invocation_ref"] == "tool:toolu_edit_1"

    started_events = [p for s, p in store.states if s == "tool_started"]
    finished_events = [p for s, p in store.states if s == "tool_finished"]
    assert len(started_events) == 1 and started_events[0]["invocation_id"] == "toolu_edit_1"
    assert len(finished_events) == 1 and finished_events[0]["invocation_id"] == "toolu_edit_1"


def test_native_unavailable_falls_back_to_text_loop(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    fake_provider = FakeNativeProvider(raise_on_start=NativeLoopUnavailable("mcp failed"))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    assert runtime.run("t1", turn=store.turn).state == "completed" and fake_provider.stream_calls >= 1


def test_cli_reported_model_replaces_turn_model(tmp_path: Path) -> None:
    store = FakeStore(model_id="claude-opus-5-5")
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    switched_models: list[str] = []

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init("claude-opus-5-5[1m]")
        sink.on_init(init)
        sink.on_text("Ok.")
        return NativeTurnOutcome(result=_make_result(text="Ok."), init=init, final_text="Ok.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        model_switched=switched_models.append,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    res = runtime.run("t1", turn=store.turn)
    assert res.state == "completed"
    assert store.turn["model_id"] == "claude-opus-5-5[1m]"
    assert switched_models == ["claude-opus-5-5[1m]"]
    model_switch_events = [p for s, p in store.states if s == "model_switched"]
    assert len(model_switch_events) == 1
    assert model_switch_events[0]["from_model"] == "claude-opus-5-5"
    assert model_switch_events[0]["to_model"] == "claude-opus-5-5[1m]"
    assert model_switch_events[0]["reason"] == "cli_reported"


def test_on_init_ignores_placeholder_provider_model_names(tmp_path: Path) -> None:
    store = FakeStore(model_id="gpt-6.1-terra")
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    switched_models: list[str] = []

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init("codex")
        sink.on_init(init)
        sink.on_text("Ok.")
        return NativeTurnOutcome(result=_make_result(text="Ok."), init=init, final_text="Ok.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        model_switched=switched_models.append,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    res = runtime.run("t1", turn=store.turn)
    assert res.state == "completed"
    assert store.turn["model_id"] == "gpt-6.1-terra"
    assert switched_models == []
    model_switch_events = [p for s, p in store.states if s == "model_switched"]
    assert model_switch_events == []


def test_context_meter_uses_cli_usage_and_catalog_window(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)

    usage = CliUsage(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=50000,
        cache_creation_input_tokens=2000,
    )

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("Measured.")
        sink.on_assistant_usage("msg_ctx_1", usage)
        return NativeTurnOutcome(
            result=_make_result(text="Measured.", usage=usage),
            init=init,
            final_text="Measured.",
            exit_code=0,
        )

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        context_reporting=True,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    assert runtime.run("t1", turn=store.turn).state == "completed"
    ctx_events = [p for s, p in store.states if s == "context_updated"]
    assert len(ctx_events) >= 2
    last_ctx = ctx_events[-1]
    assert last_ctx["used_tokens"] == 53000
    assert last_ctx["limit_tokens"] == 1_000_000


def test_usage_recorded_once_per_assistant_message(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    usage = CliUsage(input_tokens=500, output_tokens=100, cache_read_input_tokens=1000)

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("msg_dup", usage)
        sink.on_assistant_usage("msg_dup", usage)
        sink.on_text("Done.")
        return NativeTurnOutcome(result=_make_result(text="Done.", usage=usage), init=init, final_text="Done.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    assert runtime.run("t1", turn=store.turn).state == "completed"
    assert len(store.usages) == 1
    assert store.usages[0]["input_tokens"] == 1500
    assert store.usages[0]["output_tokens"] == 100
    assert runtime.counters.input_tokens == 1500


def test_stop_hook_block_sends_follow_up_on_same_handle(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    bus = LifecycleBus(base={"session_id": "c1"})
    stop_calls = 0

    def _on_stop(ev) -> LifecycleDecision | None:
        nonlocal stop_calls
        if ev.name is HookEvent.STOP:
            stop_calls += 1
            if stop_calls == 1:
                return LifecycleDecision(blocked=True, reason="Run tests before stopping")
        return None

    bus.subscribe(_on_stop)
    toolset = _make_toolset(workspace, lifecycle=bus)

    def _round_1(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("First attempt.")
        return NativeTurnOutcome(result=_make_result(text="First attempt."), init=init, final_text="First attempt.", exit_code=0)

    def _round_2(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        sink.on_text("Second attempt after hook.")
        return NativeTurnOutcome(result=_make_result(text="Second attempt after hook."), init=_make_init(req.model), final_text="Second attempt after hook.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round_1, _round_2]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        lifecycle=bus,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"
    assert fake_provider.starts == 1
    handle = fake_provider.handles[0]
    assert len(handle.sent_users) == 1
    assert "Run tests before stopping" in str(handle.sent_users[0])


def test_waiting_subagents_continues_turn_when_background_agent_finishes(tmp_path: Path) -> None:
    import threading
    from agentos.agentic.background_agents import BACKGROUND_AGENTS, BackgroundAgentEntry

    BACKGROUND_AGENTS.clear_for_tests()
    store = FakeStore()
    conv_id = str(store.turn["conversation_id"])
    workspace = ConversationWorkspace(tmp_path, conv_id)
    toolset = _make_toolset(workspace)

    bg_entry = BackgroundAgentEntry(
        agent_id="agent:c1:antigravity",
        conversation_id=conv_id,
        name="antigravity",
        output_file=".orin/agent-outputs/antigravity.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    BACKGROUND_AGENTS.register(bg_entry)

    def _round_1(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("Iniciei o Antigravity, aguardando.")
        threading.Timer(
            0.05,
            lambda: BACKGROUND_AGENTS.finish(
                "agent:c1:antigravity", "completed", "Lógica implementada com sucesso."
            ),
        ).start()
        return NativeTurnOutcome(
            result=_make_result(text="Iniciei o Antigravity, aguardando."),
            init=init,
            final_text="Iniciei o Antigravity, aguardando.",
            exit_code=0,
        )

    def _round_2(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        sink.on_text("Recebi o relatório e concluí.")
        return NativeTurnOutcome(
            result=_make_result(text="Recebi o relatório e concluí."),
            init=_make_init(req.model),
            final_text="Recebi o relatório e concluí.",
            exit_code=0,
        )

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round_1, _round_2]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    try:
        out = runtime.run("t1", turn=store.turn)
        assert out.state == "completed"
        waiting_events = [p for s, p in store.states if s == "waiting_subagents"]
        assert len(waiting_events) == 1
        assert waiting_events[0]["agents"] == ["antigravity"]
        handle = fake_provider.handles[0]
        assert len(handle.sent_users) == 1
        sent_text = str(handle.sent_users[0])
        assert '<subagent-finished agent_id="agent:c1:antigravity" name="antigravity" status="completed">' in sent_text
        assert "Lógica implementada com sucesso." in sent_text
        assert "Relatório: .orin/agent-outputs/antigravity.md" in sent_text
        assert "</subagent-finished>" in sent_text
    finally:
        BACKGROUND_AGENTS.clear_for_tests()


def test_max_turns_gets_one_closing_follow_up_then_iteration_limit(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)

    def _round_max_turns(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        return NativeTurnOutcome(
            result=_make_result(subtype="error_max_turns", is_error=True, text=""),
            init=init,
            final_text="",
            exit_code=0,
        )

    fake_provider = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round_max_turns, _round_max_turns])
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "failed"
    assert out.error_code == "ITERATION_LIMIT"
    handle = fake_provider.handles[0]
    assert len(handle.refused_reasons) >= 1
    assert len(handle.sent_users) == 1


def test_repeated_tool_failure_aborts_native_turn(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        for i in range(1, 4):
            cid = f"toolu_fail_{i}"
            sink.on_tool_use_started(cid, "mcp__orin__Read")
            sink.on_tool_use_complete(cid, "mcp__orin__Read", {"file_path": "missing.txt"})
            res = req.executor.call_tool(
                ToolCallRequest(
                    name="Read",
                    arguments={"file_path": "missing.txt"},
                    meta={TOOL_USE_ID_META_KEY: cid, "message_id": f"msg_{i}"},
                )
            )
            if i == 3:
                assert res.is_error is True
                assert "turn stopped" in res.text
        return NativeTurnOutcome(result=_make_result(text="Stopped."), init=init, final_text="Stopped.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "failed"
    assert out.error_code == "REPEATED_TOOL_FAILURE"


def test_cancel_returns_cancelled_and_closes_handle(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    is_cancelled = False

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        nonlocal is_cancelled
        init = _make_init(req.model)
        sink.on_init(init)
        is_cancelled = True
        return NativeTurnOutcome(
            result=_make_result(subtype="cancelled", is_error=False, text=""),
            init=init,
            final_text="",
            exit_code=-15,
        )

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        cancelled=lambda _t: is_cancelled,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "cancelled"
    assert fake_provider.handles[0].closed is True


def test_404_switches_to_fallback_model_without_retry(tmp_path: Path) -> None:
    store = FakeStore(model_id="claude-opus-5-5")
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)

    fallback_provider = FakeNativeProvider()
    err_404 = ProviderTransportError("Model not found", status_code=404, exhausted=False)
    primary_provider = FakeNativeProvider(raise_on_start=err_404)

    runtime = AgenticTurnRuntime(
        store=store,
        provider=primary_provider,
        toolset=toolset,
        fallback_models=("claude-sonnet-5-5",),
        fallback_provider_factory=lambda model: fallback_provider,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=2,
            context_window_tokens=1_000_000,
        ),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"
    assert primary_provider.starts == 1
    assert fallback_provider.starts == 1
    assert fallback_provider.requests[0].model == "claude-sonnet-5-5"
    retrying_events = [p for s, p in store.states if s == "retrying"]
    assert retrying_events == []
    switched_events = [p for s, p in store.states if s == "model_switched"]
    assert len(switched_events) == 1
    assert switched_events[0]["to_model"] == "claude-sonnet-5-5"


def test_history_written_for_next_turn(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "hello.txt").write_text("world\n", encoding="utf-8")
    toolset = _make_toolset(workspace)

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_tool_use_started("toolu_read_99", "mcp__orin__Read")
        sink.on_tool_use_complete("toolu_read_99", "mcp__orin__Read", {"file_path": "hello.txt"})
        res = req.executor.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "hello.txt"},
                meta={TOOL_USE_ID_META_KEY: "toolu_read_99"},
            )
        )
        assert not res.is_error
        sink.on_text("Read world.")
        return NativeTurnOutcome(result=_make_result(text="Read world."), init=init, final_text="Read world.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"
    call_steps = [s for s in store.steps if s["kind"] == "assistant_tool_call"]
    result_steps = [s for s in store.steps if s["kind"] == "tool_result"]
    assert len(call_steps) == 1
    assert len(result_steps) == 1
    assert call_steps[0]["payload"]["calls"][0]["id"] == "toolu_read_99"
    assert result_steps[0]["payload"]["id"] == "toolu_read_99"
    assert result_steps[0]["tool_call_id"] == "toolu_read_99"


def test_parallel_calls_of_one_message_run_as_one_batch(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    for idx in range(1, 4):
        (workspace.root / f"f{idx}.txt").write_text(f"content_{idx}\n", encoding="utf-8")
    toolset = _make_toolset(workspace)

    orig_invoke = toolset.invoke

    def _slow_invoke(name: str, arguments: Mapping[str, Any], **kwargs: Any) -> ToolOutcome:
        time.sleep(0.25)
        return orig_invoke(name, arguments, **kwargs)

    toolset.invoke = _slow_invoke  # type: ignore[method-assign]

    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    run_toolset_batches: list[list[dict[str, str]]] = []
    orig_run_toolset = runtime._run_toolset

    def _spy_run_toolset(turn: dict[str, object], calls: list[dict[str, str]]) -> list[dict[str, object]]:
        run_toolset_batches.append(list(calls))
        return orig_run_toolset(turn, calls)

    runtime._run_toolset = _spy_run_toolset  # type: ignore[method-assign]

    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [
            pool.submit(
                executor.call_tool,
                ToolCallRequest(
                    name="Read",
                    arguments={"file_path": f"f{idx}.txt"},
                    meta={TOOL_USE_ID_META_KEY: f"toolu_r{idx}", "message_id": "msg_batch_1"},
                ),
            )
            for idx in range(1, 4)
        ]
        results = [f.result(timeout=5.0) for f in futs]
    elapsed = time.monotonic() - t0

    assert len(run_toolset_batches) == 1
    assert len(run_toolset_batches[0]) == 3
    for idx, res in enumerate(results, start=1):
        assert res.is_error is False
        assert f"content_{idx}" in res.text
    # 3 reads of 0.25s each + 0.1s gather window should finish well under 2 * 0.25 + 0.15 = 0.65s
    assert elapsed < 0.65


def test_a_write_call_runs_immediately_without_waiting_for_a_window(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "edit_me.txt").write_text("before\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    # Read first so Phase 1 read-before-edit is satisfied
    toolset.invoke("read_file", {"path": "edit_me.txt"})

    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    run_toolset_batches: list[list[dict[str, str]]] = []
    orig_run_toolset = runtime._run_toolset

    def _spy_run_toolset(turn: dict[str, object], calls: list[dict[str, str]]) -> list[dict[str, object]]:
        run_toolset_batches.append(list(calls))
        return orig_run_toolset(turn, calls)

    runtime._run_toolset = _spy_run_toolset  # type: ignore[method-assign]

    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(
        runtime,
        store.turn,
        tool_schemas=schemas,
        gather_seconds=2.0,  # large gather window to prove write does not wait for it
    )

    t0 = time.monotonic()
    res = executor.call_tool(
        ToolCallRequest(
            name="Edit",
            arguments={"file_path": "edit_me.txt", "old_string": "before", "new_string": "after"},
            meta={TOOL_USE_ID_META_KEY: "toolu_w1", "message_id": "msg_w1"},
        )
    )
    elapsed = time.monotonic() - t0

    assert res.is_error is False
    assert len(run_toolset_batches) == 1
    assert len(run_toolset_batches[0]) == 1
    assert elapsed < 0.5


def test_reads_arriving_after_the_window_form_a_new_batch(tmp_path: Path) -> None:
    batches: list[list[str]] = []

    def _run_batch(entries, message_id: str) -> list[ToolCallResult]:
        batches.append([e.call_id for e in entries])
        return [ToolCallResult(text=f"ok:{e.call_id}") for e in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.05)
    r1 = collector.submit("c1", "read_file", {"path": "a.txt"}, message_id="m1", read_only=True)
    time.sleep(0.08)
    r2 = collector.submit("c2", "read_file", {"path": "b.txt"}, message_id="m1", read_only=True)

    assert r1.text == "ok:c1"
    assert r2.text == "ok:c2"
    assert batches == [["c1"], ["c2"]]


def test_batches_of_different_messages_never_mix() -> None:
    batches: list[tuple[str, list[str]]] = []

    def _run_batch(entries, message_id: str) -> list[ToolCallResult]:
        batches.append((message_id, [e.call_id for e in entries]))
        return [ToolCallResult(text=f"{message_id}:{e.call_id}") for e in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(collector.submit, "c_m1", "read_file", {"path": "a.txt"}, message_id="m1", read_only=True)
        f2 = pool.submit(collector.submit, "c_m2", "read_file", {"path": "b.txt"}, message_id="m2", read_only=True)
        res1 = f1.result(timeout=2.0)
        res2 = f2.result(timeout=2.0)

    assert res1.text == "m1:c_m1"
    assert res2.text == "m2:c_m2"
    assert len(batches) == 2
    seen_msgs = {m: ids for m, ids in batches}
    assert seen_msgs["m1"] == ["c_m1"]
    assert seen_msgs["m2"] == ["c_m2"]


def test_tools_list_marks_read_only_tools_with_read_only_hint(tmp_path: Path) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas)

    specs = executor.list_tools()
    assert len(specs) > 0
    for spec in specs:
        assert spec.read_only == toolset.is_read_only(spec.name), f"Mismatch on {spec.name}"

    import httpx
    from agentos.providers.cli_tools_mcp import BackgroundTurnToolServer

    bg = BackgroundTurnToolServer.start(TurnToolServer(executor))
    handle = bg.handle
    try:
        http_resp = httpx.post(
            handle.url,
            headers={"Authorization": f"Bearer {handle.token}"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
            timeout=5.0,
        )
        assert http_resp.status_code == 200
        resp = http_resp.json()
    finally:
        bg.close()
    listed = resp["result"]["tools"]
    by_name = {item["name"]: item for item in listed}
    for item in listed:
        assert item["annotations"]["readOnlyHint"] == toolset.is_read_only(item["name"])
    assert by_name["Read"]["annotations"]["readOnlyHint"] is True
    assert by_name["Edit"]["annotations"]["readOnlyHint"] is False


def test_same_message_read_and_edit_of_unread_file_is_rejected_with_read_in_same_batch(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "blind.py").write_text("x = 1\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.01)

    # Read and Edit in the same message_id "msg_blind_1"
    r_read = executor.call_tool(
        ToolCallRequest(
            name="Read",
            arguments={"file_path": "blind.py"},
            meta={TOOL_USE_ID_META_KEY: "tu_r1", "message_id": "msg_blind_1"},
        )
    )
    assert r_read.is_error is False

    r_edit = executor.call_tool(
        ToolCallRequest(
            name="Edit",
            arguments={"file_path": "blind.py", "old_string": "x = 1", "new_string": "x = 2"},
            meta={TOOL_USE_ID_META_KEY: "tu_e1", "message_id": "msg_blind_1"},
        )
    )
    assert r_edit.is_error is True
    assert "mesmo lote" in r_edit.text or "same batch" in r_edit.text.lower()
    assert (workspace.root / "blind.py").read_text(encoding="utf-8") == "x = 1\n"

    # In the NEXT assistant message ("msg_blind_2"), Edit succeeds because file was read in msg_blind_1
    r_edit_next = executor.call_tool(
        ToolCallRequest(
            name="Edit",
            arguments={"file_path": "blind.py", "old_string": "x = 1", "new_string": "x = 2"},
            meta={TOOL_USE_ID_META_KEY: "tu_e2", "message_id": "msg_blind_2"},
        )
    )
    assert r_edit_next.is_error is False
    assert (workspace.root / "blind.py").read_text(encoding="utf-8") == "x = 2\n"


def test_without_message_ids_a_read_does_not_block_edits_in_later_batches(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "blind.py").write_text("x = 1\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.01)

    r_read = executor.call_tool(
        ToolCallRequest(
            name="Read",
            arguments={"file_path": "blind.py"},
            meta={TOOL_USE_ID_META_KEY: "tu_r1"},
        )
    )
    assert r_read.is_error is False

    r_edit = executor.call_tool(
        ToolCallRequest(
            name="Edit",
            arguments={"file_path": "blind.py", "old_string": "x = 1", "new_string": "x = 2"},
            meta={TOOL_USE_ID_META_KEY: "tu_e1"},
        )
    )
    assert r_edit.is_error is False
    assert (workspace.root / "blind.py").read_text(encoding="utf-8") == "x = 2\n"


def test_without_message_ids_read_and_edit_in_one_gathered_batch_is_still_refused(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "blind.py").write_text("x = 1\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.08)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_read = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "blind.py"},
                meta={TOOL_USE_ID_META_KEY: "tu_r1"},
            ),
        )
        time.sleep(0.01)
        f_edit = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Edit",
                arguments={"file_path": "blind.py", "old_string": "x = 1", "new_string": "x = 2"},
                meta={TOOL_USE_ID_META_KEY: "tu_e1"},
            ),
        )
        r_read = f_read.result(timeout=2.0)
        r_edit = f_edit.result(timeout=2.0)

    assert r_read.is_error is False
    assert r_edit.is_error is True
    assert "same batch" in r_edit.text.lower()
    assert (workspace.root / "blind.py").read_text(encoding="utf-8") == "x = 1\n"


def test_with_real_message_ids_behavior_is_unchanged(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "blind.py").write_text("x = 1\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.01)

    r_read = executor.call_tool(
        ToolCallRequest(
            name="Read",
            arguments={"file_path": "blind.py"},
            meta={TOOL_USE_ID_META_KEY: "tu_r1", "message_id": "m1"},
        )
    )
    assert r_read.is_error is False

    r_edit = executor.call_tool(
        ToolCallRequest(
            name="Edit",
            arguments={"file_path": "blind.py", "old_string": "x = 1", "new_string": "x = 2"},
            meta={TOOL_USE_ID_META_KEY: "tu_e1", "message_id": "m1"},
        )
    )
    assert r_edit.is_error is True
    assert "same batch" in r_edit.text.lower()
    assert (workspace.root / "blind.py").read_text(encoding="utf-8") == "x = 1\n"


def test_tool_rounds_counts_per_batch_without_message_ids_and_per_message_with_ids(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    for idx in range(3):
        (workspace.root / f"f{idx}.txt").write_text(f"v{idx}\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))

    exec_no_ids = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.01)
    for idx in range(3):
        res = exec_no_ids.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": f"f{idx}.txt"},
                meta={TOOL_USE_ID_META_KEY: f"tu_no_{idx}"},
            )
        )
        assert res.is_error is False
    assert exec_no_ids.tool_rounds == 3

    exec_with_ids = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.01)
    for idx in range(3):
        res = exec_with_ids.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": f"f{idx}.txt"},
                meta={TOOL_USE_ID_META_KEY: f"tu_id_{idx}", "message_id": "m_same"},
            )
        )
        assert res.is_error is False
    assert exec_with_ids.tool_rounds == 1


def test_write_barrier_waits_for_closing_read_batch_even_with_multiple_concurrent_writes() -> None:
    import threading as _th

    order: list[str] = []
    read_entered_batch = _th.Event()
    allow_read_finish = _th.Event()

    def _run_batch(entries, message_id: str) -> list[ToolCallResult]:
        ids = [e.call_id for e in entries]
        if "r1" in ids:
            read_entered_batch.set()
            allow_read_finish.wait(timeout=2.0)
        order.extend(ids)
        return [ToolCallResult(text=f"ok:{e.call_id}") for e in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.02)
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_r1 = pool.submit(collector.submit, "r1", "read_file", {}, message_id="m1", read_only=True)
        # Wait until r1's gather window expires and enters _run_batch
        assert read_entered_batch.wait(timeout=2.0)
        f_w1 = pool.submit(collector.submit, "w1", "edit_file", {}, message_id="m1", read_only=False)
        f_w2 = pool.submit(collector.submit, "w2", "edit_file", {}, message_id="m1", read_only=False)
        time.sleep(0.03)
        allow_read_finish.set()
        f_r1.result(timeout=2.0)
        f_w1.result(timeout=2.0)
        f_w2.result(timeout=2.0)

    assert order[0] == "r1"
    assert set(order[1:]) == {"w1", "w2"}


def test_deferred_mcp_tool_and_empty_tool_schemas_validation(tmp_path: Path) -> None:
    import httpx
    from agentos.providers.cli_tools_mcp import BackgroundTurnToolServer

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    # 1. Empty tool_schemas=[] must reject unknown tools
    empty_exec = RuntimeToolExecutor(runtime, store.turn, tool_schemas=[])
    denied = empty_exec.call_tool(ToolCallRequest(name="Read", arguments={"file_path": "a.txt"}, meta={}))
    assert denied.is_error is True
    assert "Unknown tool" in denied.text

    # 2. Deferred MCP tool activated mid-turn via active_mcp_tool_names passes through TurnToolServer
    # Activation records the toolset's internal name (the property is read-only).
    toolset._active_mcp_tool_names.add("read_file")
    bg = BackgroundTurnToolServer.start(TurnToolServer(empty_exec))
    try:
        (workspace.root / "dyn.txt").write_text("dynamic_ok\n", encoding="utf-8")
        resp = httpx.post(
            bg.handle.url,
            headers={"Authorization": f"Bearer {bg.handle.token}"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "Read", "arguments": {"file_path": "dyn.txt"}, "_meta": {}},
            },
            timeout=5.0,
        ).json()
    finally:
        bg.close()
    assert resp["result"]["isError"] is False, resp
    assert "dynamic_ok" in resp["result"]["content"][0]["text"]


def test_thinking_delta_does_not_block_retry_on_transient_failure_and_compaction_releases_read_stubs(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "stub.txt").write_text("linha 1\nlinha 2\n", encoding="utf-8")
    toolset = _make_toolset(workspace)

    attempts = 0

    def _make_handle(req: NativeTurnRequest) -> ScriptedHandle:
        def _round(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
            nonlocal attempts
            attempts += 1
            init = _make_init(r.model)
            sink.on_init(init)
            sink.on_thinking("")
            sink.on_thinking("thinking before any text or tool...")
            if attempts == 1:
                raise ProviderTransportError("529 Overloaded", status_code=529, exhausted=False)
            # First read populates read stub state
            res1 = r.executor.call_tool(
                ToolCallRequest("Read", {"file_path": "stub.txt"}, {TOOL_USE_ID_META_KEY: "tu_s1", "message_id": "m1"})
            )
            assert "linha 1" in res1.text
            # Compaction happens -> must release read stubs so re-reading returns full content
            sink.on_compacted()
            res2 = r.executor.call_tool(
                ToolCallRequest("Read", {"file_path": "stub.txt"}, {TOOL_USE_ID_META_KEY: "tu_s2", "message_id": "m2"})
            )
            assert "linha 1" in res2.text
            assert "File unchanged since last read" not in res2.text
            return NativeTurnOutcome(result=_make_result(text="Recovered!"), init=init, final_text="Recovered!", exit_code=0)

        return ScriptedHandle(req, [_round])

    fake_provider = FakeNativeProvider(handle_factory=_make_handle)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=2,
            context_window_tokens=1_000_000,
        ),
    )
    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"
    assert attempts == 2


def test_max_turns_when_process_already_exited_and_caveats_on_closing_gate_failure(
    tmp_path: Path,
) -> None:
    # 1. Process already exited on error_max_turns (send_user raises RuntimeError) -> ITERATION_LIMIT
    store1 = FakeStore()
    workspace1 = ConversationWorkspace(tmp_path / "w1", "c1")
    workspace1.root.mkdir(parents=True, exist_ok=True)
    toolset1 = _make_toolset(workspace1)

    class _DeadAfterMaxTurnsHandle(ScriptedHandle):
        def is_process_alive(self) -> bool:
            return False

        def send_user(self, content: list[dict[str, Any]]) -> None:
            raise RuntimeError("Claude CLI process is already closed")

    def _round_dead(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        return NativeTurnOutcome(
            result=_make_result(subtype="error_max_turns", is_error=True, text=""),
            init=init,
            final_text="",
            exit_code=1,
        )

    prov1 = FakeNativeProvider(handle_factory=lambda req: _DeadAfterMaxTurnsHandle(req, [_round_dead]))
    rt1 = AgenticTurnRuntime(
        store=store1,
        provider=prov1,
        toolset=toolset1,
        limits=AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=1_000_000),
    )
    out1 = rt1.run("t1", turn=store1.turn)
    assert out1.state == "failed"
    assert out1.error_code == "ITERATION_LIMIT"

    # 2. Closing summary after error_max_turns with unverified changes -> completed with verification="unverified"
    store2 = FakeStore()
    workspace2 = ConversationWorkspace(tmp_path / "w2", "c2")
    workspace2.root.mkdir(parents=True, exist_ok=True)
    toolset2 = _make_toolset(workspace2)
    toolset2._changed_paths.add("a.py")

    def _round_closing(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        return NativeTurnOutcome(
            result=_make_result(subtype="success", is_error=False, text="Resumo final parcial."),
            init=_make_init(req.model),
            final_text="Resumo final parcial.",
            exit_code=0,
        )

    prov2 = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round_dead, _round_closing])
    )
    rt2 = AgenticTurnRuntime(
        store=store2,
        provider=prov2,
        toolset=toolset2,
        limits=AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=1_000_000),
    )
    out2 = rt2.run("t1", turn=store2.turn)
    assert out2.state == "completed"
    assert out2.verification == "unverified"
    assert out2.budget_exhausted is True
    assert not any("Ressalva de verificação" in d for d in store2.deltas)


def test_token_and_cost_limits_enforced_in_native_turn(tmp_path: Path) -> None:
    from decimal import Decimal

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("m_cost", CliUsage(input_tokens=5000, output_tokens=1000))
        return NativeTurnOutcome(result=_make_result(text="Done"), init=init, final_text="Done", exit_code=0)

    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    rt = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        cost_estimator=lambda inp, out: Decimal("2.50"),
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_cost=Decimal("1.00"),
            context_window_tokens=1_000_000,
        ),
    )
    out = rt.run("t1", turn=store.turn)
    assert out.state == "failed"
    assert out.error_code == "PROVIDER_COST_LIMIT"


def test_native_history_avoids_orin_tool_call_and_session_plan_cli_flags_and_final_text_fallback(
    tmp_path: Path,
) -> None:
    from agentos.agentic.native_turn import SessionPlan

    assert SessionPlan().cli_flags() == ()

    store = FakeStore()
    store.history_for_turn = lambda _t: [  # type: ignore[method-assign]
        {"role": "user", "content": "Turno anterior"},
        {
            "role": "assistant",
            "content": "Vou ler",
            "tool_calls": [
                {
                    "id": "call_prev_1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": '{"file_path":"a.txt"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_prev_1", "name": "Read", "content": "ok"},
        {"role": "user", "content": "Turno atual"},
    ]
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)

    def _round_no_delta(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        # No sink.on_text(...) called before returning NativeTurnOutcome with final_text
        return NativeTurnOutcome(
            result=_make_result(text="Texto apenas no result packet."),
            init=init,
            final_text="Texto apenas no result packet.",
            exit_code=0,
        )

    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round_no_delta]))
    rt = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    out = rt.run("t1", turn=store.turn)
    assert out.state == "completed"
    # 1. No <orin_tool_call> tag injected into native user_content
    sent_blocks_text = "\n".join(
        str(b.get("text") or "") for b in prov.requests[0].user_content if isinstance(b, Mapping)
    )
    assert "<orin_tool_call" not in sent_blocks_text
    assert "</orin_tool_call>" not in sent_blocks_text
    # 2. final_text fallback was emitted to store.deltas
    assert "Texto apenas no result packet." in "".join(store.deltas)




def test_user_mcp_tool_names_keep_their_own_prefix(tmp_path: Path) -> None:
    """Only Orin's own server prefix (mcp__orin__) is stripped; a user MCP tool keeps its qualified name."""
    from agentos.agentic.native_turn import _strip_mcp_prefix

    assert _strip_mcp_prefix("mcp__orin__Read") == "Read"
    assert _strip_mcp_prefix("mcp__orin__mcp__github__create_issue") == "mcp__github__create_issue"
    assert _strip_mcp_prefix("mcp__github__create_issue") == "mcp__github__create_issue"
    assert _strip_mcp_prefix("Read") == "Read"

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schema = {
        "type": "function",
        "function": {
            "name": "mcp__github__create_issue",
            "description": "Create an issue",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=[schema])
    assert executor.is_known_tool("mcp__github__create_issue") is True
    assert executor.is_known_tool("create_issue") is False


def _usage_runtime(tmp_path: Path, rounds: list[Callable[..., NativeTurnOutcome]], **kw: Any) -> tuple[AgenticTurnRuntime, FakeStore]:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, list(rounds)))
    limits = kw.pop("limits")
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=toolset, limits=limits, **kw)
    return rt, store


def test_native_provider_token_limit_fires_and_repeated_message_usage_counts_once(tmp_path: Path) -> None:
    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        # The CLI repeats one message's usage on every content block: it counts once.
        for _ in range(5):
            sink.on_assistant_usage("m_same", CliUsage(input_tokens=400, output_tokens=100))
        return NativeTurnOutcome(result=_make_result(text="Done"), init=init, final_text="Done", exit_code=0)

    rt, _ = _usage_runtime(
        tmp_path / "a",
        [_round],
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_tokens=600, context_window_tokens=1_000_000),
    )
    assert rt.run("t1", turn=rt.store.turn).state == "completed"

    rt2, _ = _usage_runtime(
        tmp_path / "b",
        [_round],
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_tokens=499, context_window_tokens=1_000_000),
    )
    out = rt2.run("t1", turn=rt2.store.turn)
    assert out.state == "failed"
    assert out.error_code == "PROVIDER_TOKEN_LIMIT"


def test_native_max_budget_usd_fires_and_charges_the_shared_meter_once(tmp_path: Path) -> None:
    from decimal import Decimal

    from agentos.agentic.loop_settings import BudgetMeter

    seen: list[tuple[object, object]] = []

    def _estimate(inp: object, out: object) -> Decimal:
        seen.append((inp, out))
        return Decimal("0.40")

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("m1", CliUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=90))
        sink.on_assistant_usage("m1", CliUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=90))
        sink.on_assistant_usage("m2", CliUsage(input_tokens=10, output_tokens=5))
        return NativeTurnOutcome(result=_make_result(text="Done"), init=init, final_text="Done", exit_code=0)

    meter = BudgetMeter()
    rt, _ = _usage_runtime(
        tmp_path,
        [_round],
        cost_estimator=_estimate,
        budget_meter=meter,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, max_budget_usd=Decimal("0.80"), context_window_tokens=1_000_000
        ),
    )
    out = rt.run("t1", turn=rt.store.turn)
    assert out.state == "failed"
    assert out.error_code == "MAX_BUDGET_USD"
    # Two distinct messages priced once each, with (context input incl. cache, output).
    assert seen == [(100, 5), (10, 5)]
    assert meter.total == Decimal("0.80")


def test_native_turn_ages_prior_tool_results_and_caps_prior_history_preserving_current_user(
    tmp_path: Path,
) -> None:
    import json

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    captured_requests: list[NativeTurnRequest] = []

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        captured_requests.append(req)
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("Resposta do turno 2.")
        return NativeTurnOutcome(
            result=_make_result(text="Resposta do turno 2."),
            init=init,
            final_text="Resposta do turno 2.",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    prior_messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": "Resumo da conversa até aqui:\n- Decisão anterior preservada.",
        },
        {"role": "user", "content": "Pergunta inicial do turno 1"},
    ]
    for idx in range(1, 95):
        call_id = f"call_{idx}"
        prior_messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": f"src/file_{idx}.ts"}),
                        },
                    }
                ],
            }
        )
        prior_messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "read_file",
                "content": f"FILE_{idx}_START " + ("x" * 12_000) + f" FILE_{idx}_END",
            }
        )
    prior_messages.append(
        {
            "role": "assistant",
            "content": "Diagnóstico do turno 1 concluído. Perguntas:\n1. Slug novo?\n2. Parar os 3 testes?",
        }
    )
    prior_messages.append(
        {
            "role": "user",
            "content": "1- pode criar novo slug\n2- pode sim, vamo parar os outros 3 testes",
        }
    )

    store.history_for_turn = lambda turn: prior_messages  # type: ignore[method-assign]

    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
        system_prompt="System prompt base",
        volatile_prompt="Volatile context",
    )

    result = runtime.run("t2", turn=store.turn)
    assert result.state == "completed"
    assert len(captured_requests) == 1

    blocks = captured_requests[0].user_content
    combined = "\n\n".join(str(b.get("text") or "") for b in blocks)

    # 1. Prior tool results must be aged to AGED_TOOL_RESULT_CHARS (not 12,000 chars each)
    assert "x" * 5_000 not in combined
    # 2. Oldest tool results must be evicted so prior history fits under MAX_NATIVE_PRIOR_HISTORY_CHARS (120k),
    #    while recent tool results and user/assistant/system blocks remain intact.
    assert "FILE_1_START" not in combined
    assert "FILE_94_START" in combined
    # 3. Compaction summary (system message that is not runtime system_prompt/volatile_prompt) must be preserved
    assert "Resumo da conversa até aqui" in combined
    assert "Pergunta inicial do turno 1" in combined
    # 4. Prior assistant diagnosis and current user reply must be intact at the end
    assert "Diagnóstico do turno 1 concluído." in combined
    assert (
        "[User]\n1- pode criar novo slug\n2- pode sim, vamo parar os outros 3 testes"
        in combined
    )
    # 5. Total combined payload must stay safely below Antigravity's 190KB truncation threshold
    assert len(combined.encode("utf-8")) < 135_000


def test_antigravity_retroactive_tool_events_do_not_emit_generating_tool_call_or_shift_pending_queue(
    tmp_path: Path,
) -> None:
    from agentos.providers.antigravity_cli import AntigravityCliAdapter

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "a.txt").write_text("hello a\n", encoding="utf-8")
    (workspace.root / "b.txt").write_text("hello b\n", encoding="utf-8")
    toolset = _make_toolset(workspace)

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        # In Antigravity (agy), MCP tool calls execute BEFORE step_update(state=DONE) arrives on stdout
        req.executor.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "a.txt"},
            )
        )
        sink.on_tool_use_started("conv:1", "Read")
        sink.on_tool_use_complete("conv:1", "Read", {"file_path": "a.txt"})

        req.executor.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "b.txt"},
            )
        )
        sink.on_tool_use_started("conv:2", "Read")
        sink.on_tool_use_complete("conv:2", "Read", {"file_path": "b.txt"})

        sink.on_text("Leitura concluída.")
        return NativeTurnOutcome(
            result=_make_result(text="Leitura concluída."),
            init=init,
            final_text="Leitura concluída.",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    prov.adapter = AntigravityCliAdapter()  # type: ignore[attr-defined]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
    )

    result = runtime.run("t1", turn=store.turn)
    assert result.state == "completed"

    gen_events = [s for s in store.states if s[0] == "generating_tool_call"]
    assert gen_events == []
    started_events = [s[1] for s in store.states if s[0] == "tool_started"]
    assert len(started_events) == 2
    # Second tool call must NOT inherit the first tool call's retroactive conv:1 id
    assert started_events[1].get("invocation_id") != "conv:1"


def test_antigravity_multi_step_usage_reports_latest_step_context_window_tokens_not_cumulative_sum(
    tmp_path: Path,
) -> None:
    import json
    from agentos.providers.antigravity_cli import AntigravityStreamTranslator

    tr = AntigravityStreamTranslator()
    tr.feed_line(
        json.dumps(
            {
                "event": "init",
                "conversation_id": "conv-ctx",
                "init": {"model": "gemini-4-argon-high", "tools": []},
            }
        )
    )
    for step_idx, inp_tok in ((1, 400_000), (2, 450_000), (3, 500_000)):
        tr.feed_line(
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-ctx",
                        "step_index": step_idx,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "text_delta": f"step {step_idx} ",
                        "usage": {
                            "input_tokens": inp_tok,
                            "output_tokens": 100,
                            "thinking_tokens": 10,
                            "cache_read_tokens": 300_000,
                        },
                    },
                }
            )
        )
    tr.feed_line(
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-ctx",
                    "status": "SUCCESS",
                    "response": "step 1 step 2 step 3 ",
                    "duration_seconds": 1.0,
                    "num_turns": 1,
                    "usage": {
                        "input_tokens": 1_350_000,
                        "output_tokens": 300,
                        "thinking_tokens": 30,
                        "cache_read_tokens": 900_000,
                    },
                },
            }
        )
    )
    assert tr.result is not None
    usage = tr.result.usage
    assert usage.context_tokens == 1_350_000
    assert usage.context_window_used_tokens == 500_000

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("msg_agy_1", usage)
        sink.on_text("Done")
        return NativeTurnOutcome(
            result=_make_result(text="Done", usage=usage),
            init=init,
            final_text="Done",
            exit_code=0,
        )

    rt, store = _usage_runtime(
        tmp_path,
        [_round],
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
    )
    out = rt.run("t1", turn=store.turn)
    assert out.state == "completed"
    # Billing usage still records cumulative 1,350,000 input tokens
    assert store.usages[-1]["input_tokens"] == 1_350_000
    # Context window meter uses latest step's 500,000 input tokens (50% of 1M), not 1,350,000
    ctx_events = [s[1] for s in store.states if s[0] == "context_updated"]
    assert ctx_events[-1]["used_tokens"] == 500_000
    assert ctx_events[-1]["percentage"] == 50


def test_adversarial_native_turn_huge_prose_history_and_large_current_user_message(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    captured_requests: list[NativeTurnRequest] = []

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        captured_requests.append(req)
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("OK")
        return NativeTurnOutcome(
            result=_make_result(text="OK"),
            init=init,
            final_text="OK",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    prior_messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "Resumo estruturado preservado."}],
        },
    ]
    # 30 pure-prose exchanges (no tool calls) totaling ~240,000 chars
    for idx in range(30):
        prior_messages.append(
            {"role": "user", "content": f"PROSE_USER_{idx} " + ("u" * 4_000)}
        )
        prior_messages.append(
            {"role": "assistant", "content": f"PROSE_ASST_{idx} " + ("a" * 4_000)}
        )
    # Plus a 50,000-char current user message with a marker at the very end
    large_cur_user = ("CUR_BODY " * 5_500) + "FINAL_USER_TAIL_MARKER"
    prior_messages.append({"role": "user", "content": large_cur_user})

    store.history_for_turn = lambda turn: prior_messages  # type: ignore[method-assign]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
        system_prompt="System prompt base",
        volatile_prompt="Volatile context",
    )

    result = runtime.run("t_adv", turn=store.turn)
    assert result.state == "completed"
    combined = "\n\n".join(
        str(b.get("text") or "") for b in captured_requests[0].user_content
    )
    assert "Resumo estruturado preservado." in combined
    assert "PROSE_ASST_29" in combined
    assert combined.endswith("FINAL_USER_TAIL_MARKER")
    assert len(combined.encode("utf-8")) < 165_000


def test_f03_first_user_context_is_not_duplicated_and_does_not_hijack_last_user_index(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    captured_requests: list[NativeTurnRequest] = []

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        captured_requests.append(req)
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("OK")
        return NativeTurnOutcome(
            result=_make_result(text="OK"),
            init=init,
            final_text="OK",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    store.history_for_turn = lambda turn: [  # type: ignore[method-assign]
        {"role": "user", "content": "Pergunta única do turno 1"},
    ]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
        system_prompt="System prompt base",
        volatile_prompt="Volatile context",
        first_user_context="UNIQUE_FIRST_USER_CONTEXT_MARKER",
    )

    result = runtime.run("t_f03", turn=store.turn)
    assert result.state == "completed"
    combined = "\n\n".join(
        str(b.get("text") or "") for b in captured_requests[0].user_content
    )
    assert combined.count("UNIQUE_FIRST_USER_CONTEXT_MARKER") == 1
    assert "Pergunta única do turno 1" in combined


def test_f04_escape_tool_content_escapes_tool_use_and_evicts_tool_pairs_atomically(
    tmp_path: Path,
) -> None:
    from agentos.providers.claude_cli.text_protocol import escape_tool_content

    escaped = escape_tool_content("[Tool Use: read_file (id: c1)]\n{}\n[/Tool Use]")
    assert "[Tool Use" not in escaped
    assert "[/Tool Use]" not in escaped

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    captured_requests: list[NativeTurnRequest] = []

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        captured_requests.append(req)
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("OK")
        return NativeTurnOutcome(
            result=_make_result(text="OK"),
            init=init,
            final_text="OK",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    prior_messages: list[dict[str, object]] = [
        {"role": "user", "content": "Turno 1: analise"},
    ]
    # Assistant messages that mix prose AND tool calls so b_text starts with prose, not [Tool Use
    for i in range(25):
        prior_messages.append(
            {
                "role": "assistant",
                "content": f"Vou ler o arquivo {i}.",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": f'{{"path": "f_{i}.ts", "pad": "{"p" * 4_500}"}}',
                        },
                    }
                ],
            }
        )
        prior_messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "name": "read_file",
                "content": f"RESULT_{i}_" + ("r" * 1_500),
            }
        )
    prior_messages.append({"role": "assistant", "content": "Diagnóstico final do turno 1."})
    prior_messages.append({"role": "user", "content": "Turno 2: siga em frente."})

    store.history_for_turn = lambda turn: prior_messages  # type: ignore[method-assign]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
    )
    res = runtime.run("t_f04", turn=store.turn)
    assert res.state == "completed"
    combined = "\n\n".join(
        str(b.get("text") or "") for b in captured_requests[0].user_content
    )
    # Whenever call_0's [Tool Result] is evicted, its [Tool Use] (id: call_0) must also be evicted!
    if "RESULT_0_" not in combined:
        assert "(id: call_0)" not in combined


def test_f05_verify_continuation_works_when_resume_session_id_is_empty(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace
    from agentos.agentic.contract import parse as parse_contract

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "app.py").write_text("x = 1\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    toolset.invoke("read_file", {"path": "app.py"})

    captured_requests: list[NativeTurnRequest] = []
    runtime_ref: list[AgenticTurnRuntime] = []

    def _empty_sid_result(text: str) -> CliResult:
        res = _make_result(text=text)
        return CliResult(
            subtype=res.subtype,
            is_error=res.is_error,
            result_text=res.result_text,
            stop_reason=res.stop_reason,
            num_turns=res.num_turns,
            duration_ms=res.duration_ms,
            session_id="",
            api_error_status=res.api_error_status,
            usage=res.usage,
            model_usage=res.model_usage,
        )

    def _round1(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        captured_requests.append(req)
        # Note: session_id is "" (empty) and persistent_stdin is False!
        init = CliInit(
            session_id="",
            model=req.model,
            api_key_source="none",
            mcp_servers={"orin": "connected"},
            tools=(),
        )
        sink.on_init(init)
        assert req.executor is not None
        req.executor.call_tool(
            ToolCallRequest(
                name="Edit",
                arguments={"file_path": "app.py", "old_string": "x = 1", "new_string": "x = 2"},
                meta={TOOL_USE_ID_META_KEY: "tu_edit_1", "message_id": "m1"},
            )
        )
        sink.on_text("Editei sem verificar.")
        return NativeTurnOutcome(
            result=_empty_sid_result("Editei sem verificar."),
            init=init,
            final_text="Editei sem verificar.",
            exit_code=0,
        )

    def _round2(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        captured_requests.append(req)
        init = CliInit(
            session_id="",
            model=req.model,
            api_key_source="none",
            mcp_servers={"orin": "connected"},
            tools=(),
        )
        sink.on_init(init)
        if runtime_ref:
            runtime_ref[0].contract = None
        sink.on_text("Verificado.")
        return NativeTurnOutcome(
            result=_empty_sid_result("Verificado."),
            init=init,
            final_text="Verificado.",
            exit_code=0,
        )

    handles = [_round1, _round2]
    call_idx = [0]

    def _make_handle(req: NativeTurnRequest) -> ScriptedHandle:
        fn = handles[min(call_idx[0], len(handles) - 1)]
        call_idx[0] += 1
        return ScriptedHandle(req, [fn])

    prov = FakeNativeProvider(handle_factory=_make_handle)
    prov.adapter = SimpleNamespace(  # type: ignore[attr-defined]
        name="antigravity",
        capabilities=SimpleNamespace(
            persistent_stdin=False,
            supports_session_resume=True,
            tool_list_changed=False,
        ),
    )
    from agentos.agentic.phases import PhaseController

    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        phase_controller=PhaseController(resumed_contract=True),
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
    )
    runtime.contract = parse_contract(
        {
            "objective": "Atualizar app.py",
            "acceptance": [{"id": "c1", "check": "x == 2", "how": "inspection"}],
            "toolkits": ["files"],
        }
    )
    runtime_ref.append(runtime)
    out = runtime.run("t_f05", turn=store.turn)
    assert out.state == "completed"
    # Must have spawned a 2nd process for verification even without resume_session_id
    assert len(captured_requests) == 2
    second_combined = "\n\n".join(
        str(b.get("text") or "") for b in captured_requests[1].user_content
    )
    assert "Editei sem verificar." in second_combined
    assert "report_verification" in second_combined


def test_f06_antigravity_trailing_zero_usage_done_packet_does_not_zero_context_tokens() -> None:
    import json
    from agentos.providers.antigravity_cli import AntigravityStreamTranslator

    tr = AntigravityStreamTranslator()
    tr.feed_line(
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-ctx",
                    "step_index": 2,
                    "state": "GENERATING",
                    "step_type": "agent_response",
                    "text_delta": "hello",
                    "usage": {
                        "input_tokens": 420_000,
                        "output_tokens": 150,
                        "thinking_tokens": 20,
                        "cache_read_tokens": 200_000,
                    },
                },
            }
        )
    )
    # Trailing DONE packet for the same step_index=2 with zeroed usage fields
    tr.feed_line(
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-ctx",
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "thinking_tokens": 0,
                        "cache_read_tokens": 0,
                    },
                },
            }
        )
    )
    tr.feed_line(
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-ctx",
                    "status": "SUCCESS",
                    "response": "hello",
                    "usage": {
                        "input_tokens": 420_000,
                        "output_tokens": 150,
                        "thinking_tokens": 20,
                        "cache_read_tokens": 200_000,
                    },
                },
            }
        )
    )
    assert tr.result is not None
    assert tr.result.usage.context_window_used_tokens == 420_000
    assert tr.result.usage.context_tokens == 420_000


def test_f07_codex_adapter_does_not_leave_stale_pending_tool_uses_on_complete(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "a.txt").write_text("alpha", encoding="utf-8")
    toolset = _make_toolset(workspace)

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        # Codex emits on_tool_use_started when item.started arrives, then MCP executes,
        # and then Codex emits on_tool_use_complete when item.completed arrives AFTER MCP!
        sink.on_tool_use_started("item_1", "mcp__orin__Read", message_id="msg_1")
        assert req.executor is not None
        req.executor.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "a.txt"},
            )
        )
        sink.on_tool_use_complete(
            "item_1", "mcp__orin__Read", {"file_path": "a.txt"}, message_id="msg_1"
        )
        # Second tool call without explicit tool_use_id must NOT steal "item_1"!
        sink.on_tool_use_started("item_2", "mcp__orin__Read", message_id="msg_1")
        req.executor.call_tool(
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "a.txt"},
            )
        )
        sink.on_tool_use_complete(
            "item_2", "mcp__orin__Read", {"file_path": "a.txt"}, message_id="msg_1"
        )
        sink.on_text("Pronto")
        return NativeTurnOutcome(
            result=_make_result(text="Pronto"),
            init=init,
            final_text="Pronto",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    prov.adapter = SimpleNamespace(name="codex", capabilities=None)  # type: ignore[attr-defined]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=prov,
        toolset=toolset,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, context_window_tokens=1_000_000
        ),
    )
    out = runtime.run("t_codex", turn=store.turn)
    assert out.state == "completed"
    started_events = [s[1] for s in store.states if s[0] == "tool_started"]
    assert len(started_events) == 2
    assert started_events[1]["invocation_id"] != "item_1"
    assert started_events[0]["invocation_id"] != started_events[1]["invocation_id"]




def _long_prior_history() -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    for idx in range(1, 31):
        history.append({"role": "user", "content": f"Pedido {idx}: " + ("p" * 3_000), "_sequence": 2 * idx - 1})
        history.append({"role": "assistant", "content": f"Resposta {idx}: " + ("r" * 3_000), "_sequence": 2 * idx})
    history.append({"role": "user", "content": "Pedido atual", "_sequence": 61})
    return history


def _compaction_runtime(
    tmp_path: Path, **kw: Any
) -> tuple[AgenticTurnRuntime, FakeStore, FakeNativeProvider, list[dict[str, Any]]]:
    store = FakeStore()
    history = _long_prior_history()
    store.history_for_turn = lambda turn: [dict(item) for item in history]  # type: ignore[method-assign]
    boundaries: list[dict[str, Any]] = []
    store.record_compact_boundary = lambda turn, **values: boundaries.append(dict(values))  # type: ignore[attr-defined]
    provider = FakeNativeProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        context_reporting=True,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
        **kw,
    )
    return runtime, store, provider, boundaries


def test_native_turn_compacts_prior_history_over_the_cap_and_persists_the_boundary(tmp_path: Path) -> None:
    from agentos.agentic.compaction import COMPACTION_HEADING

    runtime, store, provider, boundaries = _compaction_runtime(tmp_path)

    assert runtime.run("t1", turn=store.turn).state == "completed"

    # Summarized and recorded, so the next turn starts from the summary instead of re-sending (and
    # silently cutting) the same history again.
    assert boundaries and boundaries[0]["kind"] == "compact" and boundaries[0]["summary"]
    assert 0 < int(boundaries[0]["through_sequence"]) < 61
    combined = "\n".join(str(block.get("text") or "") for block in provider.requests[0].user_content)
    assert COMPACTION_HEADING in combined
    assert "Pedido atual" in combined and "Resposta 30:" in combined
    assert "Resposta 1: " not in combined and "Resposta 28: " in combined
    assert "context_compacted" in [state for state, _ in store.states]
    # The first meter reading already measures the compacted history.
    first_ctx = next(payload for state, payload in store.states if state == "context_updated")
    assert first_ctx["compaction_count"] == 1


def test_native_turn_respects_autocompact_off(tmp_path: Path) -> None:
    runtime, store, provider, boundaries = _compaction_runtime(tmp_path, autocompact=False)

    assert runtime.run("t1", turn=store.turn).state == "completed"
    assert boundaries == [] and provider.stream_calls == 0


def test_cli_compaction_is_reported_to_the_ui(tmp_path: Path) -> None:
    store = FakeStore()

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_compacted()
        sink.on_text("ok")
        return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

    provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        context_reporting=True,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    assert runtime.run("t1", turn=store.turn).state == "completed"
    compacted = [payload for state, payload in store.states if state == "context_compacted"]
    assert compacted and compacted[0]["compaction_count"] == 1 and compacted[0]["source"] == "cli"


def test_production_confirmation_refusals_do_not_end_the_native_turn(tmp_path: Path) -> None:
    """Three refused production commands used to abort the turn and take every tool from the model mid-answer."""
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace, interactive_permissions=False)
    texts: list[str] = []

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        for i in range(1, 5):
            cid = f"toolu_prod_{i}"
            args = {"command": f"vercel --prod --yes --name app{i}", "description": "ship"}
            sink.on_tool_use_started(cid, "mcp__orin__Bash")
            sink.on_tool_use_complete(cid, "mcp__orin__Bash", args)
            res = req.executor.call_tool(
                ToolCallRequest(name="Bash", arguments=args, meta={TOOL_USE_ID_META_KEY: cid, "message_id": f"msg_{i}"})
            )
            texts.append(res.text)
        return NativeTurnOutcome(result=_make_result(text="Pedi confirmação."), init=init, final_text="Pedi confirmação.", exit_code=0)

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    runtime = AgenticTurnRuntime(
        store=store, provider=fake_provider, toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=20, context_window_tokens=1_000_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.error_code != "REPEATED_TOOL_FAILURE"
    assert all("Nada foi executado" in text for text in texts), texts
    assert not any("turn stopped" in text for text in texts)


def test_waiting_subagents_wakes_early_when_user_queues_message(tmp_path: Path) -> None:
    import threading
    from agentos.agentic.background_agents import BACKGROUND_AGENTS, BackgroundAgentEntry

    BACKGROUND_AGENTS.clear_for_tests()
    store = FakeStore()
    store.queued_inputs = []  # type: ignore[attr-defined]

    def _claim_queued(turn: Mapping[str, object]) -> list[dict[str, object]]:
        items = list(store.queued_inputs)  # type: ignore[attr-defined]
        store.queued_inputs.clear()  # type: ignore[attr-defined]
        return items

    store.claim_queued_inputs = _claim_queued  # type: ignore[attr-defined]

    conv_id = str(store.turn["conversation_id"])
    workspace = ConversationWorkspace(tmp_path, conv_id)
    toolset = _make_toolset(workspace)

    bg_entry = BackgroundAgentEntry(
        agent_id="agent:c1:slow",
        conversation_id=conv_id,
        name="codex",
        output_file=".orin/agent-outputs/codex.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    BACKGROUND_AGENTS.register(bg_entry)

    def _round_1(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("Aguardando codex.")
        threading.Timer(
            0.05,
            lambda: store.queued_inputs.append(  # type: ignore[attr-defined]
                {"queue_id": "q1", "content": "Mude a cor do botão para verde"}
            ),
        ).start()
        return NativeTurnOutcome(
            result=_make_result(text="Aguardando codex."),
            init=init,
            final_text="Aguardando codex.",
            exit_code=0,
        )

    def _round_2(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        BACKGROUND_AGENTS.finish("agent:c1:slow", "completed", "Pronto.")
        BACKGROUND_AGENTS.take_unnotified(conv_id)
        sink.on_text("Recebi a mensagem do usuário.")
        return NativeTurnOutcome(
            result=_make_result(text="Recebi a mensagem do usuário."),
            init=_make_init(req.model),
            final_text="Recebi a mensagem do usuário.",
            exit_code=0,
        )

    fake_provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round_1, _round_2]))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    try:
        t0 = time.monotonic()
        out = runtime.run("t1", turn=store.turn)
        elapsed = time.monotonic() - t0
        assert out.state == "completed"
        assert elapsed < 2.0
        handle = fake_provider.handles[0]
        assert len(handle.sent_users) == 1
        assert "Mude a cor do botão para verde" in str(handle.sent_users[0])
    finally:
        BACKGROUND_AGENTS.clear_for_tests()


def test_foreground_subagent_promotes_to_background_on_queued_user_input_and_t4_delivers_result(
    tmp_path: Path,
) -> None:
    import threading
    from agentos.agentic.background_agents import BACKGROUND_AGENTS
    from agentos.agentic.session import TurnSession
    from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
    from agentos.persistence.postgres.schema import metadata
    from agentos.persistence.sqlite import create_local_engine
    from tests.unit.agentic.test_parallel_and_background_subagents import (
        TURN,
        _RecordingStore,
        _text_batch,
    )

    BACKGROUND_AGENTS.clear_for_tests()
    engine = create_local_engine(f"sqlite:///{(tmp_path / 't6.db').as_posix()}")
    metadata.create_all(engine)

    child_gate = threading.Event()

    class _SlowChild:
        def stream(self, _request: Any):
            child_gate.wait(timeout=5.0)
            return iter(_text_batch("Relatório final do filho promovido."))

    store = _RecordingStore()
    store.queued_inputs = []  # type: ignore[attr-defined]

    def _has_queued(_turn: Mapping[str, object]) -> bool:
        return bool(store.queued_inputs)  # type: ignore[attr-defined]

    def _claim_queued(_turn: Mapping[str, object]) -> list[dict[str, object]]:
        items = list(store.queued_inputs)  # type: ignore[attr-defined]
        store.queued_inputs.clear()  # type: ignore[attr-defined]
        return items

    store.has_queued_inputs = _has_queued  # type: ignore[attr-defined]
    store.claim_queued_inputs = _claim_queued  # type: ignore[attr-defined]

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=ConversationAgentStore(
            engine,
            conversation_id=TURN["conversation_id"],
            user_id=TURN["user_id"],
        ),
        memory_store=None,
        provider_factory=lambda: _SlowChild(),
        workspace_root=tmp_path,
        child_provider_factory=lambda _model: _SlowChild(),
    )

    try:
        threading.Timer(
            0.1,
            lambda: store.queued_inputs.append(  # type: ignore[attr-defined]
                {"queue_id": "q_user_1", "content": "Use porta 4000 em vez de 3000"}
            ),
        ).start()

        t0 = time.monotonic()
        outcome = session._run_subagent(
            subagent_type="general-purpose",
            task="Implemente o servidor",
            run_in_background=False,
        )
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0
        assert outcome.status == "succeeded"
        assert "continues in the background (agent_id=" in outcome.content
        assert "The user wrote: Use porta 4000 em vez de 3000" in outcome.content
        assert store.queued_inputs == []  # type: ignore[attr-defined]

        # Now the promoted child is in BACKGROUND_AGENTS as running; release it and verify T4 receives it
        conv_id = str(TURN["conversation_id"])
        assert len(BACKGROUND_AGENTS.running(conv_id)) == 1
        child_gate.set()
        woke = BACKGROUND_AGENTS.wait_any(conv_id, timeout=3.0, cancelled=lambda: False)
        assert woke is True
        unnotified = BACKGROUND_AGENTS.take_unnotified(conv_id)
        assert len(unnotified) == 1
        assert unnotified[0].status == "completed"
        assert "Relatório final do filho promovido." in unnotified[0].summary
    finally:
        child_gate.set()
        session.close_background_subagents()
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


def test_native_turn_emits_two_waiting_model_events_when_clock_advances_45s_without_cli_event(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    now = [datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)]

    def _clock() -> datetime:
        return now[0]

    def _round_with_45s_silence(
        req: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        now[0] = now[0] + timedelta(seconds=45)
        req.cancel_check()
        sink.on_text("Done after thinking.")
        return NativeTurnOutcome(
            result=_make_result(text="Done after thinking."),
            init=init,
            final_text="Done after thinking.",
            exit_code=0,
        )

    store = FakeStore()
    provider = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round_with_45s_silence])
    )
    toolset = _make_toolset(ConversationWorkspace(tmp_path, "c1"))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(deadline=timedelta(minutes=5)),
        clock=_clock,
    )

    res = runtime.run("t1")
    assert res.state == "completed"
    waiting_model_events = [
        payload for state, payload in store.states if state == "waiting_model"
    ]
    assert len(waiting_model_events) == 2
    assert [ev["seconds"] for ev in waiting_model_events] == [20, 40]


def test_read_plus_two_concurrent_writes_in_same_burst_without_message_id_both_rejected_as_same_batch(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    ws = ConversationWorkspace(tmp_path, "conv-burst-2w")
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws.root / "b.py").write_text("y = 2\n", encoding="utf-8")
    toolset = _make_toolset(ws)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, gather_seconds=0.08)

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_r1 = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "a.py"},
                meta={TOOL_USE_ID_META_KEY: "tu_r1"},
            ),
        )
        f_r2 = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "b.py"},
                meta={TOOL_USE_ID_META_KEY: "tu_r2"},
            ),
        )
        time.sleep(0.01)
        f_w1 = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Edit",
                arguments={"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 10"},
                meta={TOOL_USE_ID_META_KEY: "tu_w1"},
            ),
        )
        f_w2 = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Edit",
                arguments={"file_path": "b.py", "old_string": "y = 2", "new_string": "y = 20"},
                meta={TOOL_USE_ID_META_KEY: "tu_w2"},
            ),
        )
        r_r1 = f_r1.result(timeout=2.0)
        r_r2 = f_r2.result(timeout=2.0)
        r_w1 = f_w1.result(timeout=2.0)
        r_w2 = f_w2.result(timeout=2.0)

    assert r_r1.is_error is False
    assert r_r2.is_error is False
    assert r_w1.is_error is True
    assert r_w2.is_error is True
    assert "same batch" in r_w1.text.lower() or "mesmo lote" in r_w1.text.lower()
    assert "same batch" in r_w2.text.lower() or "mesmo lote" in r_w2.text.lower()
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (ws.root / "b.py").read_text(encoding="utf-8") == "y = 2\n"


def test_native_turn_suppresses_waiting_model_while_tool_runs_and_on_text_arrival(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    now = [datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)]

    def _clock() -> datetime:
        return now[0]

    ws = ConversationWorkspace(tmp_path, "c-hb")
    (ws.root / "f.txt").write_text("hello\n", encoding="utf-8")

    def _round_tool_and_late_text(
        req: NativeTurnRequest, sink: NativeTurnSink, _h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        # Advance clock 25s right before text arrives without cancel_check polling:
        # _note_cli_event should reset the timer rather than emitting waiting_model on text arrival.
        now[0] = now[0] + timedelta(seconds=25)
        sink.on_text("Immediate text after silence.")
        return NativeTurnOutcome(
            result=_make_result(text="Immediate text after silence."),
            init=init,
            final_text="Immediate text after silence.",
            exit_code=0,
        )

    store = FakeStore()
    provider = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round_tool_and_late_text])
    )
    toolset = _make_toolset(ws)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(deadline=timedelta(minutes=5)),
        clock=_clock,
    )
    res = runtime.run("t1")
    assert res.state == "completed"
    waiting_model_events = [
        payload for state, payload in store.states if state == "waiting_model"
    ]
    assert waiting_model_events == []





def _leak_executor(tmp_path: Path, **kwargs: Any) -> tuple[ConversationWorkspace, RuntimeToolExecutor]:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "page.tsx").write_text("x = 1\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=50, max_actions=50, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    kwargs.setdefault("gather_seconds", 0.01)
    kwargs.setdefault("same_message_read_window", 0.02)
    return workspace, RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, **kwargs)


def _read(executor: RuntimeToolExecutor, tool_use_id: str) -> ToolCallResult:
    return executor.call_tool(
        ToolCallRequest(name="Read", arguments={"file_path": "page.tsx"}, meta={TOOL_USE_ID_META_KEY: tool_use_id})
    )


def _edit(executor: RuntimeToolExecutor, tool_use_id: str, old: str, new: str) -> ToolCallResult:
    return executor.call_tool(
        ToolCallRequest(
            name="Edit",
            arguments={"file_path": "page.tsx", "old_string": old, "new_string": new},
            meta={TOOL_USE_ID_META_KEY: tool_use_id},
        )
    )


def test_edit_in_a_later_message_is_not_refused_when_its_call_beats_the_stream_event(tmp_path: Path) -> None:
    """Regression: the edit's MCP call can arrive before Orin parses the new message's
    stream event, so it falls back to the previous message id, whose reads leaked into it."""
    workspace, executor = _leak_executor(tmp_path)
    executor.register_tool_use_started("tu_r1", "mcp__orin__Read", message_id="msg_1")
    executor.register_tool_use_complete("tu_r1", "mcp__orin__Read", {"file_path": "page.tsx"}, message_id="msg_1")
    assert _read(executor, "tu_r1").is_error is False
    time.sleep(0.05)

    # msg_2 stream events have not been parsed yet when the Edit call arrives.
    r_edit = _edit(executor, "tu_e2", "x = 1", "x = 2")
    assert r_edit.is_error is False, r_edit.text
    assert (workspace.root / "page.tsx").read_text(encoding="utf-8") == "x = 2\n"


def test_without_any_message_id_a_read_never_blocks_an_edit_minutes_later(tmp_path: Path) -> None:
    """Regression (Antigravity): the adapter reports no message ids, only usage at the end."""
    workspace, executor = _leak_executor(tmp_path)
    assert _read(executor, "r1").is_error is False
    time.sleep(0.05)
    r_edit = _edit(executor, "e1", "x = 1", "x = 2")
    assert r_edit.is_error is False, r_edit.text
    time.sleep(0.05)
    assert _read(executor, "r2").is_error is False
    time.sleep(0.05)
    r_edit2 = _edit(executor, "e2", "x = 2", "x = 3")
    assert r_edit2.is_error is False, r_edit2.text


def test_an_edit_streamed_in_the_same_message_as_the_read_is_refused_even_when_it_arrives_late(
    tmp_path: Path,
) -> None:
    """Claude can run a read while it is still streaming the rest of the message: the edit
    that follows in that message arrives well after the read result, but is still blind."""
    workspace, executor = _leak_executor(tmp_path)
    executor.register_tool_use_started("tu_r1", "mcp__orin__Read", message_id="msg_1")
    assert _read(executor, "tu_r1").is_error is False
    executor.register_tool_use_started("tu_e1", "mcp__orin__Edit", message_id="msg_1")
    time.sleep(0.05)
    r_edit = _edit(executor, "tu_e1", "x = 1", "x = 2")
    assert r_edit.is_error is True
    assert "same batch" in r_edit.text.lower()
    assert (workspace.root / "page.tsx").read_text(encoding="utf-8") == "x = 1\n"

    executor.register_tool_use_started("tu_e2", "mcp__orin__Edit", message_id="msg_2")
    r_next = _edit(executor, "tu_e2", "x = 1", "x = 2")
    assert r_next.is_error is False, r_next.text


def test_without_stream_ids_an_edit_right_after_the_read_is_still_refused(tmp_path: Path) -> None:
    workspace, executor = _leak_executor(tmp_path, same_message_read_window=5.0)
    executor.note_message_id("msg_const")
    assert _read(executor, "r1").is_error is False
    r_edit = _edit(executor, "e1", "x = 1", "x = 2")
    assert r_edit.is_error is True
    assert (workspace.root / "page.tsx").read_text(encoding="utf-8") == "x = 1\n"


def test_a_constant_adapter_message_id_does_not_leak_reads_across_rounds(tmp_path: Path) -> None:
    """Regression: an adapter that keeps reporting the same message id for every round."""
    workspace, executor = _leak_executor(tmp_path)
    executor.note_message_id("msg_const")
    assert _read(executor, "r1").is_error is False
    time.sleep(0.05)
    r_edit = _edit(executor, "e1", "x = 1", "x = 2")
    assert r_edit.is_error is False, r_edit.text
    assert (workspace.root / "page.tsx").read_text(encoding="utf-8") == "x = 2\n"
