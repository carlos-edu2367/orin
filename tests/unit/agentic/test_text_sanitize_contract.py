"""Contract: every place that saves or sends the assistant's text goes through `text_sanitize`.

Two kinds of proof, because a list of paths rots as the code grows:

1. Behaviour. Each path gets the text of the original bug (a fake `<invoke>` call and made-up results) and what it
   saves or sends is read back clean.
2. A static scan. Every `.delta(` call site in `src/agentos` (the only way assistant text reaches the saved message)
   must be in a file known to filter, and nothing outside the sanitizer may call the raw filter functions. A new
   writer fails this test until it is routed through `agentos.agentic.text_sanitize` and listed here.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import _SubagentStore
from agentos.agentic.text_sanitize import sanitize_assistant_text
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.discord.formatter import strip_residual_speak_tags
from agentos.persistence.postgres.schema import conversation_events, conversation_messages, metadata
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.text_protocol import TranscriptState
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
    _make_toolset,
)

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "agentos"
_CASES = json.loads((ROOT / "tests" / "fixtures" / "text_tool_leak_cases.json").read_text(encoding="utf-8"))["cases"]
PRINT_INPUT = next(case["input"] for case in _CASES if case["name"].startswith("print"))
PRINT_PROSE = ("Por código: os 10 PACOTE_344", "listo os 19 restantes para ler.")
MARKUP = ("<invoke", "function_results", "</output>", "<parameter")


def _assert_clean(text: str) -> None:
    for marker in MARKUP:
        assert marker not in text, (marker, text)
    for prose in PRINT_PROSE:
        assert prose in text, (prose, text)


def _chunks(text: str, size: int = 11) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


# ---------------------------------------------------------------- behaviour, one test per path


def test_path_saved_message_is_cleaned_when_the_turn_finishes() -> None:
    """ChatStore: the assistant message every provider and every turn kind ends in."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1", message="oi", provider="openrouter", model_id="m", idempotency_key="k1"
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    for chunk in _chunks(PRINT_INPUT):  # a path that forgot to filter: the raw text is saved delta by delta
        store.delta(turn, chunk)

    store.finish(turn)

    with engine.connect() as c:
        saved = c.execute(
            select(conversation_messages.c.content).where(conversation_messages.c.message_id == turn["assistant_message_id"])
        ).scalar_one()
        resets = c.execute(
            select(conversation_events.c.payload).where(conversation_events.c.event_type == "message.reset")
        ).scalars().all()
    _assert_clean(saved)
    assert resets and resets[-1]["content"] == saved, "a connected client is told to replace the text it already shows"


def test_path_saved_message_that_is_already_clean_is_not_rewritten() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="user-1", message="oi", provider="openrouter", model_id="m", idempotency_key="k2")
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    store.delta(turn, "Resposta normal com <result>ok</result>.")
    store.finish(turn)
    with engine.connect() as c:
        kinds = c.execute(select(conversation_events.c.event_type)).scalars().all()
    assert "message.reset" not in kinds


def test_path_native_turn_stream(tmp_path: Path) -> None:
    store = FakeStore()

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        for chunk in _chunks(PRINT_INPUT):
            sink.on_text(chunk)
        return NativeTurnOutcome(result=_make_result(text="x"), init=init, final_text="x", exit_code=0)

    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round, _round])),
        toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    assert runtime.run("t1", turn=store.turn).state == "completed"
    _assert_clean("".join(store.deltas))


def test_path_text_protocol_and_http_loop_stream() -> None:
    """The classic loop (HTTP providers, the Claude text protocol): it saved the raw text before this contract."""
    deltas: list[str] = []
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}) for chunk in _chunks(PRINT_INPUT)
    ] + ['data: {"choices":[{"delta":{},"finish_reason":"stop"}]}', "data: [DONE]"]

    class Store:
        turn = {
            "turn_id": "turn-1", "conversation_id": "c1", "user_id": "u1", "workspace_id": "w1", "agent_id": "a1",
            "execution_id": "e1", "provider": "openrouter", "model_id": "m", "user_message_id": "um1",
            "assistant_message_id": "am1",
        }

        def load(self, turn_id: str) -> dict[str, object]:
            return self.turn

        def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
            return [{"role": "user", "content": "oi"}]

        def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None: ...

        def delta(self, turn: dict[str, object], text: str) -> None:
            deltas.append(text)

        def record_usage(self, turn: dict[str, object], **kwargs: object) -> None: ...

        def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None: ...

    class Provider:
        def stream(self, request: dict[str, object]):
            return normalize_sse(lines, provider="openrouter")

    result = AgenticTurnRuntime(store=Store(), provider=Provider(), toolset=SimpleNamespace(
        schemas=lambda: [], is_read_only=lambda name: False, argument_names=lambda name: frozenset(),
    )).run("turn-1")

    assert result.state == "completed"
    _assert_clean("".join(deltas))


def test_path_subagent_report_the_parent_reads_back() -> None:
    store = _SubagentStore(SimpleNamespace(), {}, "agent-1", "Pesquisador", "tarefa")
    for chunk in _chunks(PRINT_INPUT):
        store.delta({}, chunk)
    _assert_clean(store.final_answer())
    _assert_clean(store.snapshot_transcript()[-1]["content"])


def test_path_discord_message() -> None:
    _assert_clean(strip_residual_speak_tags(PRINT_INPUT))


def test_path_history_replayed_to_the_model() -> None:
    window = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": PRINT_INPUT},
        {"role": "user", "content": "e agora?"},
    ]
    rendered = "\n".join(b["text"] for b in TranscriptState().blocks_for(window, native_history=True))
    _assert_clean(rendered)


# ---------------------------------------------------------------- the scan


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


# Files whose `.delta(` calls carry model text, and how each one is covered.
DELTA_WRITERS = {
    "agentic/native_turn.py": "stream filter + sanitize_assistant_text (imports text_sanitize)",
    "agentic/runtime.py": "stream filter before store.delta (imports text_sanitize)",
    "workers/chat.py": "forwards to ChatStore.delta; ChatStore.finish sanitizes the saved message",
}
MUST_IMPORT_SANITIZER = ("agentic/native_turn.py", "agentic/runtime.py", "agentic/session.py", "conversations/chat.py", "discord/formatter.py")


def test_every_delta_call_site_is_in_a_file_known_to_sanitize() -> None:
    found: dict[str, int] = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "delta"
        )
        if count:
            found[_rel(path)] = count
    unknown = sorted(set(found) - set(DELTA_WRITERS))
    assert not unknown, (
        f"{unknown} write assistant text with `.delta(`. Route that text through agentos.agentic.text_sanitize "
        "(a stream from new_assistant_text_stream() or sanitize_assistant_text()), then list the file in DELTA_WRITERS."
    )
    assert set(DELTA_WRITERS) <= set(found), "DELTA_WRITERS lists a file that no longer writes deltas"


@pytest.mark.parametrize("relative", MUST_IMPORT_SANITIZER)
def test_writer_modules_import_the_sanitizer(relative: str) -> None:
    source = (SRC / relative).read_text(encoding="utf-8")
    assert "text_sanitize" in source, relative


def test_the_saved_message_is_sanitized_in_finish() -> None:
    source = (SRC / "conversations" / "chat.py").read_text(encoding="utf-8")
    finish = source[source.index("    def finish(self, turn: dict[str, object], *, failed"):]
    assert "_sanitize_saved_assistant_text(c, turn, now)" in finish[:1600]


def test_nothing_outside_the_sanitizer_calls_the_raw_filters() -> None:
    allowed = {"agentic/text_sanitize.py", "providers/claude_cli/text_protocol.py", "providers/claude_cli/__init__.py"}
    offenders: list[str] = []
    for path in _python_files():
        if _rel(path) in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if {"strip_residual_tool_protocol_blocks", "ResidualToolTextStreamFilter"} & set(names):
                offenders.append(_rel(path))
    assert not offenders, f"use agentos.agentic.text_sanitize instead of the raw filters: {sorted(set(offenders))}"


def test_sanitize_is_the_function_the_paths_share() -> None:
    assert sanitize_assistant_text(PRINT_INPUT) == _CASES[0]["expected"]
