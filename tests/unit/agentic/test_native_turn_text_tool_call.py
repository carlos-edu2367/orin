"""A native turn where the model typed an Anthropic-style tool call as text: it never ran, so the model is told once."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.native_turn import TEXT_TOOL_CALL_CORRECTION
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.native_loop import (
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
    _make_toolset,
)

FAKE_CALL = (
    "Vou rodar o comando.\n"
    '<function_calls><invoke name="Bash"><parameter name="command">ls</parameter></invoke></function_calls>\n'
    "<function_results><result><output>a.txt</output></result></function_results>\n"
)


def _round_with(text: str, final: str):
    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        for i in range(0, len(text), 9):
            sink.on_text(text[i : i + 9])
        return NativeTurnOutcome(result=_make_result(text=final), init=init, final_text=final, exit_code=0)

    return _round


def _run(tmp_path: Path, rounds: list) -> tuple[FakeStore, FakeNativeProvider, object]:
    store = FakeStore()
    provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, rounds))
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    return store, provider, runtime.run("t1", turn=store.turn)


def _visible(store: FakeStore) -> str:
    return "".join(str(d) for d in getattr(store, "deltas", []))


def test_fake_tool_call_text_is_hidden_and_the_model_is_told_once(tmp_path: Path) -> None:
    store, provider, out = _run(
        tmp_path,
        [_round_with(FAKE_CALL, "x"), _round_with("Agora uso a ferramenta de verdade.", "ok")],
    )
    assert out.state == "completed"
    handle = provider.handles[0]
    assert len(handle.sent_users) == 1
    assert TEXT_TOOL_CALL_CORRECTION in str(handle.sent_users[0])
    shown = _visible(store)
    assert "Vou rodar o comando." in shown
    assert "invoke" not in shown and "function_" not in shown and "a.txt" not in shown


def test_correction_is_sent_only_once_per_turn(tmp_path: Path) -> None:
    store, provider, out = _run(tmp_path, [_round_with(FAKE_CALL, "x"), _round_with(FAKE_CALL, "y")])
    assert out.state == "completed"
    assert len(provider.handles[0].sent_users) == 1


def test_plain_and_legit_xml_text_does_not_trigger_a_correction(tmp_path: Path) -> None:
    store, provider, out = _run(
        tmp_path,
        [_round_with("Use <result>ok</result> e <output>1</output> no HTML.", "ok")],
    )
    assert out.state == "completed"
    assert provider.handles[0].sent_users == []
    assert "<result>ok</result>" in _visible(store)
