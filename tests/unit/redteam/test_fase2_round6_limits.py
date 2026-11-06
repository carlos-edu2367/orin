"""Red team round 6: turn-wide max_turns (N1), runner exits (N4/N6)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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


def _rt(tmp_path: Path, factory: Any, *, gate: Any = None, **kw: Any) -> tuple[AgenticTurnRuntime, FakeStore, FakeNativeProvider]:
    store = FakeStore()
    toolset = _make_toolset(ConversationWorkspace(tmp_path, "c1"))
    if gate is not None:
        toolset.code_completion_gate = gate  # type: ignore[method-assign]
    prov = FakeNativeProvider(handle_factory=factory)
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=toolset, **kw)
    return rt, store, prov


def _usage(sink: NativeTurnSink, h: ScriptedHandle, ids: list[str]) -> None:
    for mid in ids:
        if h.closed:
            return
        sink.on_assistant_usage(mid, CliUsage(input_tokens=10, output_tokens=1))


def test_final_answer_on_exactly_the_max_turns_th_call_completes(tmp_path: Path) -> None:
    """Classic parity: max_turns calls are allowed; a final answer on the last one completes."""
    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        _usage(sink, h, ["m1", "m2", "m3"])
        sink.on_text("Resposta final.")
        return NativeTurnOutcome(result=_make_result(text="Resposta final."), init=init, final_text="Resposta final.", exit_code=0)

    rt, store, _ = _rt(tmp_path, lambda req: ScriptedHandle(req, [_r]),
                       limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=3, context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert out.state == "completed", out


def test_one_call_over_max_turns_fails_with_max_turns_and_closes_the_handle(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        _usage(sink, h, ["m1", "m2", "m3", "m4"])
        seen["closed"] = h.closed
        raise ProviderTransportError("Claude CLI exited without result packet (code=-9)", status_code=502, exhausted=False)

    rt, store, _ = _rt(tmp_path, lambda req: ScriptedHandle(req, [_r]),
                       limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=3, context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert seen["closed"] is True
    assert out.state == "failed" and out.error_code == "MAX_TURNS", out


def test_repeated_usage_of_one_message_is_one_model_call(tmp_path: Path) -> None:
    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        _usage(sink, h, ["m1", "m1", "m1", "m2", "m2"])
        return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

    rt, store, _ = _rt(tmp_path, lambda req: ScriptedHandle(req, [_r]),
                       limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=2, context_window_tokens=1_000_000))
    assert rt.run("t1", turn=store.turn).state == "completed"


def test_model_calls_survive_a_transient_restart(tmp_path: Path) -> None:
    """Calls of a process that failed before visible output still count after the retry (classic counts calls)."""
    n = {"p": 0}

    def _factory(req: NativeTurnRequest) -> ScriptedHandle:
        n["p"] += 1
        p = n["p"]

        def _r(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
            init = _make_init(r.model)
            sink.on_init(init)
            if p == 1:
                _usage(sink, h, ["p1a", "p1b"])
                raise ProviderTransportError("529", status_code=529, exhausted=False)
            _usage(sink, h, ["p2a", "p2b"])
            return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

        return ScriptedHandle(req, [_r])

    rt, store, _ = _rt(tmp_path, _factory,
                       limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=3, max_provider_retries=2,
                                            context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert n["p"] == 2
    assert out.state == "failed" and out.error_code == "MAX_TURNS", out


def test_no_follow_up_is_sent_once_max_turns_is_spent_and_gate_failure_is_a_caveat(tmp_path: Path) -> None:
    handles: list[ScriptedHandle] = []

    def _factory(req: NativeTurnRequest) -> ScriptedHandle:
        def _r(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
            init = _make_init(r.model)
            sink.on_init(init)
            _usage(sink, h, ["m1", "m2"])
            return NativeTurnOutcome(result=_make_result(text="pronto"), init=init, final_text="pronto", exit_code=0)

        h = ScriptedHandle(req, [_r])
        handles.append(h)
        return h

    rt, store, _ = _rt(tmp_path, _factory, gate=lambda: (False, "Faltou rodar os testes"),
                       limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=2, context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert handles and handles[0].sent_users == []
    assert out.state == "completed_with_caveats", out


def test_subagent_native_turn_uses_its_own_max_turns(tmp_path: Path) -> None:
    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        _usage(sink, h, ["s1", "s2"])
        return NativeTurnOutcome(result=_make_result(text="ok"), init=init, final_text="ok", exit_code=0)

    rt, store, prov = _rt(tmp_path, lambda req: ScriptedHandle(req, [_r]),
                          limits=AgenticLimits(max_iterations=None, max_actions=None, max_turns=1, context_window_tokens=1_000_000))
    store.turn["is_subagent"] = True
    out = rt.run("t1", turn=store.turn)
    assert prov.requests[0].max_turns == 1
    assert out.error_code == "MAX_TURNS", out


def test_cwd_security_abort_fails_the_turn_without_starting_or_falling_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentos.agentic.native_turn as nt

    closed: list[bool] = []
    orig_close = nt.RuntimeToolExecutor.close

    def _close(self: Any) -> None:
        closed.append(True)
        orig_close(self)

    monkeypatch.setattr(nt.RuntimeToolExecutor, "close", _close)
    monkeypatch.setattr(nt, "ensure_isolated_cwd", lambda base=None: (_ for _ in ()).throw(CliSecurityAbort("planted")))
    rt, store, prov = _rt(tmp_path, None, limits=AgenticLimits(max_iterations=5, max_actions=5, context_window_tokens=1_000_000))
    out = rt.run("t1", turn=store.turn)
    assert out.state == "failed" and out.error_code == "PROVIDER_STREAM_NOT_STARTED", out
    assert prov.starts == 0 and prov.stream_calls == 0 and closed


def test_executor_closed_on_cancel_at_top_of_loop_after_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentos.agentic.native_turn as nt

    created: list[Any] = []
    orig = nt.RuntimeToolExecutor.__init__

    def _spy(self: Any, *a: Any, **k: Any) -> None:
        orig(self, *a, **k)
        created.append(self)

    monkeypatch.setattr(nt.RuntimeToolExecutor, "__init__", _spy)
    cancelled = {"v": False}

    def _r(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        cancelled["v"] = True
        raise ProviderTransportError("529", status_code=529, exhausted=False)

    store = FakeStore()
    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_r]))
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
                            limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_retries=2, context_window_tokens=1_000_000),
                            cancelled=lambda _t: cancelled["v"])
    rt.run("t1", turn=store.turn)
    assert created and all(ex._closed.is_set() for ex in created)
