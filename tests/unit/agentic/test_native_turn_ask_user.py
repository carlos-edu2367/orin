from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.native_turn import NativeTurnRunner, RuntimeToolExecutor
from agentos.agentic.runtime import INTERRUPTED_TOOL_RESULT, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.cli_tools_mcp import ToolCallRequest
from agentos.workers.chat import _RuntimeStore


QUESTIONS: dict[str, object] = {
    "questions": [
        {
            "id": "q1",
            "question": "Qual banco de dados usar?",
            "mode": "single_choice",
            "options": [
                {"id": "sqlite", "label": "SQLite"},
                {"id": "postgres", "label": "Postgres"},
            ],
        }
    ]
}


class _RecordingStore:
    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.states: list[str] = []
        self.lifecycle_events: list[tuple[str, dict[str, object]]] = []
        self.transitions: list[str] = []
        self.steps: list[dict[str, object]] = []
        self.finished: list[dict[str, object]] = []
        self._queue: list[dict[str, str]] = []
        self._queue_lock = threading.Lock()

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.deltas.append(text)

    def reset_assistant_message(self, turn: dict[str, object]) -> None:
        self.deltas.clear()

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append({"failed": failed, "code": code})

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.states.append(state)
        self.lifecycle_events.append((state, dict(payload)))
        target = {
            "running": "RUNNING",
            "waiting_tool": "WAITING_TOOL",
            "retrying": "RUNNING",
            "waiting_user": "WAITING_USER",
        }.get(state)
        if target == "WAITING_USER":
            self.transitions.append(target)

    def effect_started(self, turn: dict[str, object], *, kind: str, invocation_ref: str, request_ref: str) -> str:
        return f"eff-{invocation_ref}"

    def effect_finished(
        self,
        turn: dict[str, object],
        *,
        effect_id: str,
        state: str,
        result_ref: str | None = None,
        error_code: str | None = None,
        private_result: object = None,
    ) -> None:
        pass

    def record_quality(self, turn: dict[str, object], **values: object) -> None:
        pass

    def record_step(self, turn: dict[str, object], **values: object) -> None:
        self.steps.append(dict(values))

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
        return [{"role": "user", "content": str(turn.get("user_content") or "oi")}]

    def claim_queued_inputs(self, turn: dict[str, object]) -> list[dict[str, str]]:
        with self._queue_lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    def enqueue_now(self, text: str, queue_id: str = "q-1") -> None:
        with self._queue_lock:
            self._queue.append({"queue_id": queue_id, "content": text})

    def queue_after(self, *, seconds: float, text: str, queue_id: str = "q-1") -> threading.Thread:
        def _worker() -> None:
            time.sleep(seconds)
            self.enqueue_now(text, queue_id=queue_id)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t


def _make_turn(workspace: Path) -> dict[str, object]:
    return {
        "turn_id": "turn-ask-1",
        "conversation_id": "conv-ask-1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "conversation_mode": "code",
        "code_mode": "code",
        "permission_mode": "accept_edits",
        "workspace_path": str(workspace),
        "user_content": "Pergunte qual banco usar",
        "history_messages": [],
    }


def _make_toolset(workspace: Path, *, permission_mode: str = "accept_edits") -> AgentToolset:
    return AgentToolset(
        ConversationWorkspace(workspace),
        code_mode_active=True,
        public_tool_names=True,
        code_mode_work_kind="analysis",
        permission_mode=permission_mode,
    )


def test_ask_user_waits_for_queued_answer_inside_the_turn(tmp_path: Path) -> None:
    store = _RecordingStore()
    toolset = _make_toolset(tmp_path)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=object(),
        toolset=toolset,
        cancelled=lambda _t: False,
        clock=time.monotonic,
    )
    turn = _make_turn(tmp_path)
    executor = RuntimeToolExecutor(
        runtime=runtime,
        turn=turn,
        messages=[],
        deadline=time.monotonic() + 10.0,
    )

    store.queue_after(seconds=0.35, text="Respostas às perguntas do agente:\nSQLite", queue_id="q-ans-1")
    result = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {}))

    assert result.is_error is False
    assert result.text.startswith("Resposta da pessoa:\nRespostas às perguntas do agente:\nSQLite")
    assert "waiting_user_in_turn" in store.states
    assert "queued_input" in store.states
    assert store.states[-1] == "running"
    assert store.transitions == []
    assert executor.abort_code is None


def test_ask_user_wait_ends_on_cancel(tmp_path: Path) -> None:
    store = _RecordingStore()
    cancelled = threading.Event()
    toolset = _make_toolset(tmp_path)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=object(),
        toolset=toolset,
        cancelled=lambda _t: cancelled.is_set(),
        clock=time.monotonic,
    )
    turn = _make_turn(tmp_path)
    executor = RuntimeToolExecutor(
        runtime=runtime,
        turn=turn,
        messages=[],
        deadline=time.monotonic() + 10.0,
    )

    def _cancel_later() -> None:
        time.sleep(0.3)
        cancelled.set()

    threading.Thread(target=_cancel_later, daemon=True).start()
    t0 = time.monotonic()
    result = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {}))
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert result.is_error is True
    assert result.text == INTERRUPTED_TOOL_RESULT
    assert executor.abort_code == "TURN_CANCELLED"


def test_plan_approval_still_ends_turn_waiting_user(tmp_path: Path) -> None:
    store = _RecordingStore()
    plan_file = tmp_path / ".orin" / "plans" / "plan.md"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text("# Plano\n1. Passo 1\n", encoding="utf-8")

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path),
        code_mode_active=True,
        public_tool_names=True,
        code_mode_work_kind="analysis",
        permission_mode="plan_mode",
        code_mode_requires_approval=True,
    )
    toolset._plan_file_path = plan_file

    class _PlanHandle:
        def __init__(self, req: Any) -> None:
            self.req = req
            self.refused = False

        def run_until_result(self, sink: Any) -> Any:
            from agentos.providers.claude_cli.native_loop import NativeTurnOutcome
            from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage

            init = CliInit(
                session_id="s-plan",
                model="claude-opus-5-5",
                api_key_source="none",
                mcp_servers={"orin": "connected"},
                tools=(),
            )
            sink.on_init(init)
            res = self.req.executor.call_tool(
                ToolCallRequest("ExitPlanMode", {"plan": "# Plano\n1. Passo 1"}, {"claudecode/toolUseId": "tu_plan"})
            )
            assert res.is_error is False
            u = CliUsage(input_tokens=50, output_tokens=20)
            return NativeTurnOutcome(
                init=init,
                result=CliResult(
                    subtype="success",
                    is_error=False,
                    result_text="Plano pronto para revisão.",
                    stop_reason="end_turn",
                    num_turns=2,
                    duration_ms=20,
                    session_id="s-plan",
                    api_error_status=None,
                    usage=u,
                    model_usage={"claude-opus-5-5": u},
                ),
                final_text="Plano pronto para revisão.",
                exit_code=0,
            )

        def refuse_tools(self, reason: str = "") -> None:
            self.refused = True

        def send_user(self, content: list[dict[str, Any]]) -> None:
            pass

        def close(self) -> None:
            pass

    class _PlanProvider:
        def native_turn_available(self, turn: Any) -> bool:
            return True

        def start_native_turn(self, req: Any) -> Any:
            return _PlanHandle(req)

    runtime = AgenticTurnRuntime(
        store=store,
        provider=_PlanProvider(),
        toolset=toolset,
        cancelled=lambda _t: False,
    )
    turn = _make_turn(tmp_path)
    turn["permission_mode"] = "plan_mode"
    outcome = runtime.run(str(turn["turn_id"]), turn=turn)

    assert outcome.state == "waiting_user"
    assert "waiting_user" in store.states
    assert "waiting_user_in_turn" not in store.states


def test_queued_input_and_reminders_ride_on_next_tool_result(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("conteudo do arquivo\n", encoding="utf-8")
    store = _RecordingStore()
    store.enqueue_now("Use tipagem estrita em tudo", queue_id="q-note-1")
    toolset = _make_toolset(tmp_path)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=object(),
        toolset=toolset,
        cancelled=lambda _t: False,
        clock=time.monotonic,
    )
    runtime.reminders.add("custom", "Always run unit tests before finishing.", key="custom:1")
    turn = _make_turn(tmp_path)
    messages: list[dict[str, object]] = []
    executor = RuntimeToolExecutor(
        runtime=runtime,
        turn=turn,
        messages=messages,
        deadline=time.monotonic() + 10.0,
    )

    result = executor.call_tool(ToolCallRequest("Read", {"file_path": "hello.txt"}, {"claudecode/toolUseId": "tu_read"}))

    assert result.is_error is False
    assert "conteudo do arquivo" in result.text
    assert "<system-reminder>" in result.text
    assert "Always run unit tests before finishing." in result.text
    assert "Use tipagem estrita em tudo" in result.text
    assert ("queued_input", {"queued_text": "Use tipagem estrita em tudo", "queue_id": "q-note-1"}) in store.lifecycle_events


def test_worker_lifecycle_ignores_waiting_user_in_turn() -> None:
    transitions: list[tuple[str, str]] = []

    class _FakeWorker:
        store = object()

        def _refresh_heartbeat_if_due(self) -> None:
            pass

        def _kernel_manages(self, turn: dict[str, object]) -> bool:
            return False

        def _transition(self, turn: dict[str, object], target: str, reason: str, *, result_ref: str | None = None) -> None:
            transitions.append((target, reason))

    turn = {"turn_id": "t-1"}
    runtime_store = _RuntimeStore(_FakeWorker(), turn)

    runtime_store.lifecycle(turn, "waiting_user_in_turn")
    assert transitions == []

    runtime_store.lifecycle(turn, "waiting_user")
    assert transitions == [("WAITING_USER", "agentic_waiting_user")]


def test_session_records_turn_waiting_user_with_in_turn_flag(tmp_path: Path) -> None:
    recorded: list[tuple[object, str, dict[str, object]]] = []
    session = object.__new__(TurnSession)
    session.turn = {"turn_id": "t-1", "agent_id": "agent:main"}
    session.main_agent_id = "agent:main"
    session._record = lambda event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None: recorded.append(  # type: ignore[method-assign]
        (event_type, summary, dict(payload or {}))
    )

    session.emit_lifecycle({"turn_id": "t-1", "agent_id": "agent:main"}, "waiting_user_in_turn")
    assert len(recorded) == 1
    event_type, summary, payload = recorded[0]
    assert str(event_type) == "turn.waiting_user" or getattr(event_type, "value", "") == "turn.waiting_user"
    assert summary == "Aguardando sua resposta"
    assert payload.get("in_turn") is True


def test_pre_queued_message_is_not_mistaken_for_ask_user_answer_and_close_stops_wait(
    tmp_path: Path,
) -> None:
    store = _RecordingStore()
    # User queued a normal message BEFORE ask_user was invoked
    store.enqueue_now("Lembrete: use Python 3.12", queue_id="q-pre")
    toolset = _make_toolset(tmp_path)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=object(),
        toolset=toolset,
        cancelled=lambda _t: False,
        clock=time.monotonic,
    )
    turn = _make_turn(tmp_path)
    executor = RuntimeToolExecutor(
        runtime=runtime,
        turn=turn,
        messages=[],
        deadline=time.monotonic() + 10.0,
    )

    store.queue_after(
        seconds=0.25,
        text="Respostas às perguntas do agente:\nPostgres",
        queue_id="q-real-ans",
    )
    result = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {}))
    assert result.is_error is False
    assert "Resposta da pessoa:\nRespostas às perguntas do agente:\nPostgres" in result.text
    assert "Lembrete: use Python 3.12" in result.text

    # Also verify executor.close() immediately stops _wait_for_answer()
    executor2 = RuntimeToolExecutor(
        runtime=runtime,
        turn=turn,
        messages=[],
        deadline=time.monotonic() + 30.0,
    )
    threading.Timer(0.15, executor2.close).start()
    t0 = time.monotonic()
    res2 = executor2.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {}))
    assert (time.monotonic() - t0) < 1.0
    assert res2.is_error is True

