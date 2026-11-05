"""Red team round 4: ask_user inside a live native turn (back-end)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.agentic import transcript
from agentos.providers.claude_cli.errors import ProviderTransportError
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
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

QUESTIONS: dict[str, object] = {
    "questions": [
        {
            "id": "q1",
            "question": "Qual banco de dados usar?",
            "mode": "single_choice",
            "options": [{"id": "sqlite", "label": "SQLite"}, {"id": "postgres", "label": "Postgres"}],
        }
    ]
}


class _QueueStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self._queue: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def claim_queued_inputs(self, turn: Any) -> list[dict[str, str]]:
        with self._lock:
            items, self._queue = list(self._queue), []
            return items

    def enqueue(self, text: str, queue_id: str) -> None:
        with self._lock:
            self._queue.append({"queue_id": queue_id, "content": text})


def test_ask_user_still_waits_for_the_answer_after_a_transient_process_restart(tmp_path: Path) -> None:
    """A 529 before any output restarts the CLI process inside the same turn (retry). The `finally:
    executor.close()` of the first attempt leaves the shared executor closed, so the ask_user of the second
    process returns immediately as interrupted and the whole turn fails, though the answer is on its way."""
    store = _QueueStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    attempts = {"n": 0}
    seen: dict[str, Any] = {}

    def _make(req: NativeTurnRequest) -> ScriptedHandle:
        def _round(r: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
            attempts["n"] += 1
            init = _make_init(r.model)
            sink.on_init(init)
            if attempts["n"] == 1:
                raise ProviderTransportError("529 Overloaded", status_code=529, exhausted=False)
            threading.Timer(0.3, store.enqueue, args=("Respostas às perguntas do agente:\nPostgres", "q-ans")).start()
            res = r.executor.call_tool(
                ToolCallRequest("AskUserQuestion", QUESTIONS, {TOOL_USE_ID_META_KEY: "tu_ask", "message_id": "m1"})
            )
            seen["is_error"] = res.is_error
            seen["text"] = res.text
            return NativeTurnOutcome(result=_make_result(text="Vou de Postgres."), init=init, final_text="Vou de Postgres.", exit_code=0)

        return ScriptedHandle(req, [_round])

    rt = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(handle_factory=_make),
        toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_retries=2, context_window_tokens=1_000_000),
        cancelled=lambda _t: False,
    )
    t0 = time.monotonic()
    out = rt.run("t1", turn=store.turn)
    assert attempts["n"] == 2
    assert seen.get("is_error") is False, f"ask_user after a retry returned {seen.get('text')!r} in {time.monotonic() - t0:.2f}s"
    assert out.state == "completed", out


def test_queued_answer_is_recorded_after_the_question_that_it_answers(tmp_path: Path) -> None:
    """The answer is claimed (and appended to messages / recorded as a queued_input step) before the
    assistant tool-call message and step of the ask_user that asked it. transcript.project() replays steps in
    recorded order, so later turns see the answer before the question."""
    store = _QueueStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    rt = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
        cancelled=lambda _t: False,
    )
    from agentos.agentic.native_turn import RuntimeToolExecutor

    messages: list[dict[str, object]] = []
    executor = RuntimeToolExecutor(rt, store.turn, messages=messages, deadline=time.monotonic() + 10.0)
    threading.Timer(0.2, store.enqueue, args=("Respostas às perguntas do agente:\nSQLite", "q-1")).start()
    res = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {TOOL_USE_ID_META_KEY: "tu_ask"}))
    assert res.is_error is False

    kinds = [s["kind"] for s in store.steps]
    assert transcript.STEP_ASSISTANT_TOOL_CALL in kinds and transcript.STEP_QUEUED_INPUT in kinds
    assert kinds.index(transcript.STEP_ASSISTANT_TOOL_CALL) < kinds.index(transcript.STEP_QUEUED_INPUT), kinds


def test_normal_message_queued_before_the_question_is_not_taken_as_the_answer(tmp_path: Path) -> None:
    store = _QueueStore()
    store.enqueue("Lembrete: Python 3.12", "q-pre")
    workspace = ConversationWorkspace(tmp_path, "c1")
    rt = AgenticTurnRuntime(
        store=store, provider=FakeNativeProvider(), toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
        cancelled=lambda _t: False,
    )
    from agentos.agentic.native_turn import RuntimeToolExecutor

    executor = RuntimeToolExecutor(rt, store.turn, messages=[], deadline=time.monotonic() + 10.0)
    threading.Timer(0.2, store.enqueue, args=("Respostas às perguntas do agente:\nSQLite", "q-ans")).start()
    res = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {TOOL_USE_ID_META_KEY: "tu_ask"}))
    assert res.is_error is False
    head = res.text.split("<system-reminder>")[0]
    assert "SQLite" in head and "Lembrete" not in head
    assert "Lembrete: Python 3.12" in res.text


def test_ask_user_wait_ends_when_the_turn_deadline_passes(tmp_path: Path) -> None:
    store = _QueueStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    rt = AgenticTurnRuntime(
        store=store, provider=FakeNativeProvider(), toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
        cancelled=lambda _t: False, clock=time.monotonic,
    )
    from agentos.agentic.native_turn import RuntimeToolExecutor

    executor = RuntimeToolExecutor(rt, store.turn, messages=[], deadline=time.monotonic() + 0.4)
    t0 = time.monotonic()
    res = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {TOOL_USE_ID_META_KEY: "tu_ask"}))
    assert time.monotonic() - t0 < 2.0
    assert res.is_error is True


def test_backend_emits_pre_queued_delivery_between_the_question_and_the_in_turn_wait(tmp_path: Path) -> None:
    """Evidence for the front-end finding: a normal message queued before ask_user is delivered (queued_input ->
    activity turn.started with queue_id) AFTER the ask_user tool_finished and BEFORE waiting_user_in_turn. The UI
    treats any queued delivery after a question as that question's answer and closes the card."""
    store = _QueueStore()
    store.enqueue("Lembrete: Python 3.12", "q-pre")
    workspace = ConversationWorkspace(tmp_path, "c1")
    rt = AgenticTurnRuntime(
        store=store, provider=FakeNativeProvider(), toolset=_make_toolset(workspace),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
        cancelled=lambda _t: False,
    )
    from agentos.agentic.native_turn import RuntimeToolExecutor

    executor = RuntimeToolExecutor(rt, store.turn, messages=[], deadline=time.monotonic() + 10.0)
    threading.Timer(0.2, store.enqueue, args=("Respostas às perguntas do agente:\nSQLite", "q-ans")).start()
    executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {TOOL_USE_ID_META_KEY: "tu_ask"}))
    names = [
        (state, payload.get("queue_id") or payload.get("tool_name"))
        for state, payload in store.states
        if state in {"tool_finished", "queued_input", "waiting_user_in_turn"}
    ]
    i_tool = names.index(("tool_finished", "ask_user"))
    i_pre = names.index(("queued_input", "q-pre"))
    i_wait = [n[0] for n in names].index("waiting_user_in_turn")
    assert i_tool < i_pre < i_wait, names
