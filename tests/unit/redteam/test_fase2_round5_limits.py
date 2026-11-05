"""Red team round 5: limits on the native path (F5/F6/F7/F14 fixes)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.errors import CliSecurityAbort, ProviderTransportError
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.stream import CliUsage
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
    _make_toolset,
)


def _rt(tmp_path: Path, rounds: list[Any], *, gate: Any = None, **kw: Any) -> tuple[AgenticTurnRuntime, FakeStore, FakeNativeProvider]:
    store = FakeStore()
    toolset = _make_toolset(ConversationWorkspace(tmp_path, "c1"))
    if gate is not None:
        toolset.code_completion_gate = gate  # type: ignore[method-assign]
    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, list(rounds)))
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=toolset, **kw)
    return rt, store, prov


def test_max_turns_is_a_cap_for_the_whole_turn_not_per_follow_up_message(tmp_path: Path) -> None:
    """--max-turns is per user message of the live process. Every follow-up the runner sends on the same stdin
    (code gate, verification, Stop hook continuations, closing) gets a fresh budget, so max_turns=3 allows
    3 x (1 + follow-ups) model calls. The classic loop counts model calls over the whole turn."""
    seq = iter(range(100))

    def _calls(n: int, sink: NativeTurnSink, h: ScriptedHandle) -> None:
        for _ in range(n):
            if h.closed:  # a killed claude -p makes no further model calls
                return
            sink.on_assistant_usage(f"m{next(seq)}", CliUsage(input_tokens=10, output_tokens=1))

    def _r1(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        _calls(2, sink, h)
        return NativeTurnOutcome(result=_make_result(text="pronto"), init=init, final_text="pronto", exit_code=0)

    def _r2(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        _calls(3, sink, h)  # fresh --max-turns budget for the follow-up message
        return NativeTurnOutcome(result=_make_result(subtype="error_max_turns", is_error=True, text=""), init=None, final_text="", exit_code=0)

    def _r3(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        _calls(1, sink, h)
        return NativeTurnOutcome(result=_make_result(text="resumo"), init=None, final_text="resumo", exit_code=0)

    gate_calls = {"n": 0}

    def _gate() -> tuple[bool, str | None]:
        gate_calls["n"] += 1
        return (gate_calls["n"] > 1, "Faltou rodar os testes")

    rt, store, prov = _rt(
        tmp_path, [_r1, _r2, _r3], gate=_gate,
        limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=3, context_window_tokens=1_000_000),
    )
    out = rt.run("t1", turn=store.turn)
    assert prov.requests[0].max_turns == 3
    assert len(store.usages) <= 4, f"{len(store.usages)} model calls for max_turns=3 (result {out})"
    assert out.state == "failed" and out.error_code == "MAX_TURNS", out


def test_cost_breach_closes_the_handle_and_reports_the_cost_code(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_assistant_usage("m1", CliUsage(input_tokens=10, output_tokens=1))
        seen["closed"] = h.closed
        raise ProviderTransportError("Claude CLI exited without result packet (code=-9)", status_code=502, exhausted=False)

    rt, store, _ = _rt(
        tmp_path, [_r], cost_estimator=lambda i, o: Decimal("5"),
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_cost=Decimal("1"), max_provider_retries=3,
                             context_window_tokens=1_000_000),
    )
    out = rt.run("t1", turn=store.turn)
    assert seen["closed"] is True
    assert out.state == "failed" and out.error_code == "PROVIDER_COST_LIMIT", out


def test_breach_followed_by_native_unavailable_is_not_a_silent_text_fallback(tmp_path: Path) -> None:
    """The breach kills the CLI; if the dying stream surfaces as NativeLoopUnavailable (no init seen yet when the
    usage arrived cannot happen, but a closed stdout before init can), the turn must still end on the limit."""
    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        sink.on_assistant_usage("m1", CliUsage(input_tokens=900_000, output_tokens=1))
        from agentos.providers.claude_cli.errors import NativeLoopUnavailable
        raise NativeLoopUnavailable("exited before initialization", status_code=503, exhausted=False)

    rt, store, prov = _rt(tmp_path, [_r], limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_tokens=1000, context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert prov.stream_calls == 0
    assert out.error_code == "PROVIDER_TOKEN_LIMIT", out


def test_security_abort_mid_turn_is_never_retried_or_moved_to_a_fallback_model(tmp_path: Path) -> None:
    starts = {"n": 0}

    def _factory(req: NativeTurnRequest) -> ScriptedHandle:
        starts["n"] += 1

        def _r(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
            raise CliSecurityAbort("apiKeySource='ANTHROPIC_API_KEY'")

        return ScriptedHandle(req, [_r])

    store = FakeStore()
    prov = FakeNativeProvider(handle_factory=_factory)
    rt = AgenticTurnRuntime(
        store=store, provider=prov, toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_retries=5, context_window_tokens=1_000_000),
        fallback_models=("claude-sonnet-5-5",),
        fallback_provider_factory=lambda *a, **k: prov,
    )
    out = rt.run("t1", turn=store.turn)
    assert starts["n"] == 1 and prov.stream_calls == 0, (starts, prov.stream_calls)
    assert out.state == "failed", out


def test_executor_is_closed_when_the_runner_leaves_after_a_retry(tmp_path: Path) -> None:
    """next_process skips executor.close() on the retry's finally; the top-of-loop cancel/deadline returns are
    outside that try, so the executor must still be closed there (no ask_user waiter can outlive the runner)."""
    import agentos.agentic.native_turn as nt

    created: list[Any] = []
    orig = nt.RuntimeToolExecutor.__init__

    def _spy(self: Any, *a: Any, **k: Any) -> None:
        orig(self, *a, **k)
        created.append(self)

    cancelled = {"v": False}

    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        cancelled["v"] = True  # the person cancels while the first process fails transiently
        raise ProviderTransportError("529", status_code=529, exhausted=False)

    import pytest

    mp = pytest.MonkeyPatch()
    mp.setattr(nt.RuntimeToolExecutor, "__init__", _spy)
    try:
        store = FakeStore()
        prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_r]))
        rt = AgenticTurnRuntime(
            store=store, provider=prov, toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
            limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_retries=2, context_window_tokens=1_000_000),
            cancelled=lambda _t: cancelled["v"],
        )
        rt.run("t1", turn=store.turn)
    finally:
        mp.undo()
    assert created and all(ex._closed.is_set() for ex in created)
