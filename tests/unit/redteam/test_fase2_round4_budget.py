"""Red team round 4: token/cost budget on the native claude -p path."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.stream import CliEventKind, CliUsage, StreamJsonParser
from agentos.providers.cli_tools_mcp import ToolCallRequest
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    TOOL_USE_ID_META_KEY,
    _make_init,
    _make_result,
    _make_toolset,
)


def _se(ev: dict[str, Any]) -> str:
    return json.dumps({"type": "stream_event", "event": ev})


def _realistic_two_block_message() -> list[str]:
    """One API message with a text block and a tool_use block, as `claude -p --include-partial-messages` streams it:
    one `assistant` packet per content block, each emitted at content_block_stop (so before message_delta),
    each repeating the message_start usage; the final output_tokens only arrive in message_delta and in result."""
    u0 = {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0, "output_tokens": 1}
    return [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s", "model": "claude-opus-5-5",
                    "apiKeySource": "none", "mcp_servers": [{"name": "orin", "status": "connected"}], "tools": []}),
        _se({"type": "message_start", "message": {"id": "msg_A", "usage": u0}}),
        _se({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Vou ler"}}),
        _se({"type": "content_block_stop", "index": 0}),
        json.dumps({"type": "assistant", "message": {"id": "msg_A", "content": [{"type": "text", "text": "Vou ler"}], "usage": u0}}),
        _se({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "mcp__orin__Read", "input": {}}}),
        _se({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"file_path\":\"a\"}"}}),
        _se({"type": "content_block_stop", "index": 1}),
        json.dumps({"type": "assistant", "message": {"id": "msg_A", "content": [{"type": "tool_use", "id": "toolu_1", "name": "mcp__orin__Read", "input": {"file_path": "a"}}], "usage": u0}}),
        _se({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 850}}),
        _se({"type": "message_stop"}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "x", "num_turns": 1,
                    "duration_ms": 1, "session_id": "s",
                    "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 850}}),
    ]


def test_usage_event_reports_the_messages_final_output_tokens() -> None:
    """The native sink bills exactly the USAGE events (deduped per message id). The parser emits USAGE at the
    first `assistant` packet, which precedes message_delta: the message's output tokens are billed as 1, not 850."""
    parser = StreamJsonParser()
    usage_events = []
    for line in _realistic_two_block_message():
        usage_events.extend(ev for ev in parser.feed_line(line) if ev.kind == CliEventKind.USAGE)
    billed_output = sum(ev.usage.output_tokens for ev in usage_events if ev.usage is not None)
    assert parser.result is not None and parser.result.usage.output_tokens == 850
    assert billed_output == 850, f"native path bills {billed_output} output tokens for a message that produced 850"


def _runtime(tmp_path: Path, rounds: list[Any], **kw: Any) -> tuple[AgenticTurnRuntime, FakeStore, FakeNativeProvider]:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "a.txt").write_text("conteudo\n", encoding="utf-8")
    toolset = _make_toolset(workspace)
    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, list(rounds)))
    limits = kw.pop("limits")
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=toolset, limits=limits, **kw)
    return rt, store, prov


def test_token_limit_breach_stops_the_cli_process_instead_of_letting_it_keep_calling_the_model(tmp_path: Path) -> None:
    """Classic loop: the call that crosses max_provider_tokens is the last one. Native path: the breach only
    refuses tools; the live `claude -p` keeps going (a refused tool_result is one more paid model call, and the
    model may retry tools until --max-turns). The process must be stopped at the breach."""
    observed: dict[str, Any] = {}

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_tool_use_started("toolu_1", "mcp__orin__Read", "m1")  # type: ignore[call-arg]
        sink.on_assistant_usage("m1", CliUsage(input_tokens=900_000, output_tokens=200))
        observed["closed_at_breach"] = h.closed
        observed["refused_at_breach"] = list(h.refused_reasons)
        # The CLI is still alive: it delivers the tool call, then sends the error back to the model (paid call 2).
        res = req.executor.call_tool(
            ToolCallRequest("Read", {"file_path": "a.txt"}, {TOOL_USE_ID_META_KEY: "toolu_1", "message_id": "m1"})
        )
        observed["tool_error"] = res.is_error
        sink.on_assistant_usage("m2", CliUsage(input_tokens=900_500, output_tokens=200))
        return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

    rt, store, _ = _runtime(
        tmp_path,
        [_round],
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_tokens=500_000, context_window_tokens=1_000_000),
    )
    out = rt.run("t1", turn=store.turn)
    assert out.state == "failed" and out.error_code == "PROVIDER_TOKEN_LIMIT"
    assert observed["tool_error"] is True
    assert observed["refused_at_breach"], "tools are refused at the breach"
    assert observed["closed_at_breach"] is True, "the claude -p process keeps running (and billing) after the limit fired"


@pytest.mark.parametrize(
    "estimate",
    [None, float("nan"), float("inf"), -5, "abc", Decimal("NaN"), "sNaN"],
)
def test_weird_cost_estimates_never_break_or_poison_the_turn(tmp_path: Path, estimate: Any) -> None:
    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("m1", CliUsage(input_tokens=10, output_tokens=5))
        sink.on_assistant_usage("m2", CliUsage(input_tokens=10, output_tokens=5))
        return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

    rt, store, _ = _runtime(
        tmp_path,
        [_round],
        cost_estimator=lambda i, o: estimate,
        limits=AgenticLimits(
            max_iterations=10, max_actions=10, max_cost=Decimal("1"), max_budget_usd=Decimal("1"),
            context_window_tokens=1_000_000,
        ),
    )
    out = rt.run("t1", turn=store.turn)
    assert out.state == "completed", out


def test_raising_estimator_never_breaks_the_turn(tmp_path: Path) -> None:
    def _boom(i: object, o: object) -> Decimal:
        raise RuntimeError("pricing down")

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("m1", CliUsage(input_tokens=10, output_tokens=5))
        return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

    rt, store, _ = _runtime(
        tmp_path, [_round], cost_estimator=_boom,
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_cost=Decimal("0.0001"), context_window_tokens=1_000_000),
    )
    assert rt.run("t1", turn=store.turn).state == "completed"


def test_usage_is_counted_once_across_a_process_restart(tmp_path: Path) -> None:
    """A transient failure before output restarts the process; the second process's messages are new ids and the
    first process's usage stays counted (the sink lives across restarts)."""
    from agentos.providers.claude_cli.errors import ProviderTransportError

    calls = {"n": 0}

    def _make(req: NativeTurnRequest) -> ScriptedHandle:
        def _round(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
            calls["n"] += 1
            init = _make_init(r.model)
            sink.on_init(init)
            if calls["n"] == 1:
                sink.on_assistant_usage("p1_m1", CliUsage(input_tokens=300, output_tokens=0))
                raise ProviderTransportError("529", status_code=529, exhausted=False)
            sink.on_assistant_usage("p2_m1", CliUsage(input_tokens=300, output_tokens=0))
            return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

        return ScriptedHandle(req, [_round])

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    rt = AgenticTurnRuntime(
        store=store, provider=FakeNativeProvider(handle_factory=_make), toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_retries=2,
                             max_provider_tokens=500, context_window_tokens=1_000_000),
    )
    out = rt.run("t1", turn=store.turn)
    assert calls["n"] == 2
    assert out.state == "failed" and out.error_code == "PROVIDER_TOKEN_LIMIT"


def test_native_path_honours_the_max_turns_limit(tmp_path: Path) -> None:
    """Code mode sets max_iterations=None and max_turns=<maxTurns setting> (session.py ~4105); subagent definitions
    set max_turns too. The classic loop fails with MAX_TURNS after that many model calls. The native runner passes
    `--max-turns int(max_iterations or 200)` and never reads limits.max_turns."""
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    prov = FakeNativeProvider()
    rt = AgenticTurnRuntime(
        store=store, provider=prov, toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=3, context_window_tokens=1_000_000),
    )
    rt.run("t1", turn=store.turn)
    assert prov.requests, "native path not taken"
    assert prov.requests[0].max_turns <= 3, f"claude -p got --max-turns {prov.requests[0].max_turns} for max_turns=3"


def test_turn_deadline_on_the_native_path_ends_as_turn_deadline_exceeded(tmp_path: Path) -> None:
    """NativeTurnHandle raises ProviderTransportError('TURN_DEADLINE_EXCEEDED: ...') when the turn deadline passes.
    The runner's generic `except Exception` turns it into reconciliation_required / PROVIDER_STREAM_INTERRUPTED once
    anything was shown, where the classic loop reports TURN_DEADLINE_EXCEEDED."""
    from agentos.providers.claude_cli.errors import ProviderTransportError

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        sink.on_init(_make_init(req.model))
        sink.on_text("Trabalhando...")
        raise ProviderTransportError("TURN_DEADLINE_EXCEEDED: Claude CLI native turn exceeded deadline", status_code=None, exhausted=False)

    rt, store, _ = _runtime(tmp_path, [_round], limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert out.error_code == "TURN_DEADLINE_EXCEEDED", out
