from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.phases import Phase, PhaseController
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
import agentos.agentic.runtime as runtime_mod
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.native_loop import (
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.providers.claude_cli.provider import NATIVE_MODES, native_tools_enabled
from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage, StreamUsage
from agentos.providers.cli_tools_mcp import ToolCallRequest
from agentos.providers.models import FinishReason


def _make_init(model: str = "claude-opus-5-5") -> CliInit:
    return CliInit(
        session_id="sess_friend",
        model=model,
        api_key_source="none",
        mcp_servers={"orin": "connected"},
        tools=("mcp__orin__read_file", "mcp__orin__write_contract"),
    )


def _make_result(text: str = "Done.") -> CliResult:
    u = CliUsage(input_tokens=80, output_tokens=20)
    return CliResult(
        subtype="success",
        is_error=False,
        result_text=text,
        stop_reason="end_turn",
        num_turns=1,
        duration_ms=40,
        session_id="sess_friend",
        api_error_status=None,
        usage=u,
        model_usage={"claude-opus-5-5": u},
    )


class _FriendStore:
    def __init__(self, *, conversation_mode: str = "friend") -> None:
        self.turn: dict[str, object] = {
            "turn_id": "t_friend",
            "conversation_id": "c_friend",
            "user_id": "u1",
            "execution_id": "e1",
            "provider": "anthropic",
            "model_id": "claude-opus-5-5",
            "user_message_id": "um1",
            "assistant_message_id": "am1",
            "code_mode": "off",
            "conversation_mode": conversation_mode,
            "live_mode": True if conversation_mode == "friend" else False,
        }
        self.states: list[tuple[str, dict[str, Any]]] = []
        self.speak_records: list[tuple[str, str, dict[str, Any]]] = []
        self.deltas: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self._eff_seq = 0

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        return [{"role": "user", "content": "Me diga o que tem no arquivo a.txt"}]

    def lifecycle(self, turn: Mapping[str, object], state: str, **payload: object) -> None:
        self.states.append((state, dict(payload)))

    def record(
        self,
        turn: Mapping[str, object],
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, object],
    ) -> None:
        self.speak_records.append((event_type.value, summary, dict(payload)))

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
        return f"eff_{self._eff_seq}"

    def effect_finished(self, turn: Mapping[str, object], effect_id: str | None, **kw: object) -> None:
        pass

    def record_step(self, turn: Mapping[str, object], **kw: object) -> None:
        self.steps.append(dict(kw))

    def add_usage(self, turn: Mapping[str, object], **kw: object) -> None:
        pass

    def finish(self, turn: Mapping[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append({"failed": failed, "code": code})


def test_native_tools_enabled_includes_chat_and_friend() -> None:
    assert NATIVE_MODES == frozenset({"code", "chat", "friend"})
    assert (
        native_tools_enabled(
            conversation_mode="chat",
            code_mode="",
            env={},
            version=(2, 1, 309),
        )
        is True
    )
    assert (
        native_tools_enabled(
            conversation_mode="friend",
            code_mode="",
            env={},
            version=(2, 1, 309),
        )
        is True
    )


def test_friend_native_turn_emits_speak_events_like_classic_loop(
    tmp_path: Path,
) -> None:
    assert hasattr(runtime_mod, "_SpeakState")
    assert hasattr(AgenticTurnRuntime, "_emit_speak_events")

    (tmp_path / "a.txt").write_text("segredo=42\n", encoding="utf-8")

    chunks_iter_1 = [
        "<speak>Olá! Vou conferir o arquivo agora.</speak>",
        "\nLendo `a.txt`...",
    ]
    chunks_iter_2 = [
        "<speak>Pronto! O valor encontrado é quarenta e dois.</speak>",
        "\nResultado: `segredo=42`.",
    ]

    # 1. Run classic loop
    classic_store = _FriendStore(conversation_mode="friend")
    classic_ws = ConversationWorkspace(tmp_path)
    classic_ts = AgentToolset(classic_ws, public_tool_names=False)

    class _ClassicProvider:
        def __init__(self) -> None:
            self.call_idx = 0

        def stream(self, _req: Mapping[str, Any], **_kw: Any) -> Any:
            self.call_idx += 1
            if self.call_idx == 1:
                for idx, ch in enumerate(chunks_iter_1, start=1):
                    yield NormalizedStreamItem(StreamKind.TEXT, idx, text=ch)
                yield NormalizedStreamItem(
                    StreamKind.TOOL_CALL,
                    10,
                    tool_call_id="call_1",
                    tool_name="read_file",
                    arguments_delta='{"path": "a.txt"}',
                )
                yield NormalizedStreamItem(
                    StreamKind.USAGE,
                    11,
                    usage=StreamUsage(input_tokens=50, output_tokens=15),
                )
                yield NormalizedStreamItem(
                    StreamKind.FINISH,
                    12,
                    finish_reason=FinishReason.TOOL_CALLS,
                )
            else:
                for idx, ch in enumerate(chunks_iter_2, start=1):
                    yield NormalizedStreamItem(StreamKind.TEXT, idx, text=ch)
                yield NormalizedStreamItem(
                    StreamKind.USAGE,
                    11,
                    usage=StreamUsage(input_tokens=80, output_tokens=20),
                )
                yield NormalizedStreamItem(
                    StreamKind.FINISH,
                    12,
                    finish_reason=FinishReason.STOP,
                )

    classic_rt = AgenticTurnRuntime(
        store=classic_store,  # type: ignore[arg-type]
        provider=_ClassicProvider(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=200_000,
        ),
        toolset=classic_ts,
    )
    classic_res = classic_rt.run("t_friend")
    assert classic_res.state == "completed"

    # 2. Run native loop with the exact same chunks and tool boundary
    native_store = _FriendStore(conversation_mode="friend")
    native_ws = ConversationWorkspace(tmp_path)
    native_ts = AgentToolset(native_ws, public_tool_names=False)

    class _NativeHandle:
        def __init__(self, req: NativeTurnRequest) -> None:
            self.req = req

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def send_user(self, content: Any) -> None:
            pass

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            init = _make_init()
            sink.on_init(init)
            for ch in chunks_iter_1:
                sink.on_text(ch)
            sink.on_tool_use_started("call_1", "mcp__orin__read_file", "msg_1")
            sink.on_tool_use_complete(
                "call_1",
                "mcp__orin__read_file",
                {"path": "a.txt"},
                "msg_1",
            )
            self.req.executor.call_tool(
                ToolCallRequest(
                    name="read_file",
                    arguments={"path": "a.txt"},
                    meta={"claudecode/toolUseId": "call_1", "message_id": "msg_1"},
                )
            )
            for ch in chunks_iter_2:
                sink.on_text(ch)
            res = _make_result(text="".join(chunks_iter_1 + chunks_iter_2))
            return NativeTurnOutcome(
                init=init,
                result=res,
                final_text=res.result_text,
                exit_code=0,
            )

    class _NativeProvider:
        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _NativeHandle:
            return _NativeHandle(req)

    native_rt = AgenticTurnRuntime(
        store=native_store,  # type: ignore[arg-type]
        provider=_NativeProvider(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=200_000,
        ),
        toolset=native_ts,
    )
    native_res = native_rt.run("t_friend")
    assert native_res.state == "completed"

    # Compare speak event sequences (types and clean_text / delta)
    classic_speak_seq = [
        (ev_type, payload.get("clean_text") or payload.get("delta") or summary)
        for ev_type, summary, payload in classic_store.speak_records
    ]
    native_speak_seq = [
        (ev_type, payload.get("clean_text") or payload.get("delta") or summary)
        for ev_type, summary, payload in native_store.speak_records
    ]
    assert len(native_speak_seq) > 0
    assert native_speak_seq == classic_speak_seq
    assert "".join(native_store.deltas) == "".join(classic_store.deltas)


def test_chat_native_turn_advances_phase_from_results(
    tmp_path: Path,
) -> None:
    store = _FriendStore(conversation_mode="chat")
    ws = ConversationWorkspace(tmp_path)
    ts = AgentToolset(ws, public_tool_names=False)
    phases = PhaseController()
    assert phases.current == Phase.ORIENT

    published_tool_names: list[str] = []

    class _ChatNativeHandle:
        def __init__(self, req: NativeTurnRequest) -> None:
            self.req = req

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def send_user(self, content: Any) -> None:
            pass

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            for spec in self.req.executor.list_tools():
                published_tool_names.append(spec.name)
            init = _make_init()
            sink.on_init(init)
            args = {
                "objective": "Summarize notes in a document",
                "deliverables": [{"path": "summary.md", "description": "Summary"}],
                "constraints": ["no extra files"],
                "acceptance": [{"id": "a1", "check": "summary exists", "how": "inspection"}],
                "toolkits": ["files"],
            }
            sink.on_tool_use_started("call_c1", "mcp__orin__write_contract", "msg_1")
            sink.on_tool_use_complete(
                "call_c1",
                "mcp__orin__write_contract",
                args,
                "msg_1",
            )
            self.req.executor.call_tool(
                ToolCallRequest(
                    name="write_contract",
                    arguments=args,
                    meta={"claudecode/toolUseId": "call_c1", "message_id": "msg_1"},
                )
            )
            sink.on_text("Contrato registrado e executado.")
            res = _make_result(text="Contrato registrado e executado.")
            return NativeTurnOutcome(
                init=init,
                result=res,
                final_text=res.result_text,
                exit_code=0,
            )

    class _ChatNativeProvider:
        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _ChatNativeHandle:
            return _ChatNativeHandle(req)

    rt = AgenticTurnRuntime(
        store=store,  # type: ignore[arg-type]
        provider=_ChatNativeProvider(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=200_000,
        ),
        toolset=ts,
        phase_controller=phases,
    )
    res = rt.run("t_friend")
    assert res.state == "completed"
    assert "write_contract" in published_tool_names
    assert phases.current == Phase.EXECUTE
    phase_events = [p for st, p in store.states if st == "phase_changed"]
    assert len(phase_events) == 1
    assert phase_events[0]["previous_phase"] == "orient"
    assert phase_events[0]["phase"] == "execute"


def test_chat_native_turn_allows_report_verification_and_blocks_write_in_verify_phase(
    tmp_path: Path,
) -> None:
    store = _FriendStore(conversation_mode="chat")
    ws = ConversationWorkspace(tmp_path)
    ts = AgentToolset(ws, public_tool_names=False)
    phases = PhaseController()

    round_num = 0
    write_in_verify_result: list[Any] = []
    report_verify_result: list[Any] = []

    class _VerifyHandle:
        def __init__(self, req: NativeTurnRequest) -> None:
            self.req = req
            self.sent_users: list[Any] = []

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def send_user(self, content: Any) -> None:
            self.sent_users.append(content)

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            nonlocal round_num
            round_num += 1
            init = _make_init()
            if round_num == 1:
                sink.on_init(init)
                # Write a file in ORIENT -> triggers _needs_verification() at end of round 1
                res_w = self.req.executor.call_tool(
                    ToolCallRequest(
                        name="write_file",
                        arguments={"path": "out.txt", "content": "hello"},
                        meta={"claudecode/toolUseId": "call_w1", "message_id": "msg_1"},
                    )
                )
                assert res_w.is_error is False
                sink.on_text("Escrevi o arquivo.")
                return NativeTurnOutcome(
                    init=init,
                    result=_make_result(text="Escrevi o arquivo."),
                    final_text="Escrevi o arquivo.",
                    exit_code=0,
                )
            # Round 2: we are now in Phase.VERIFY
            assert phases.current == Phase.VERIFY
            # Trying to write_file in VERIFY must be rejected by phase enforcement
            res_illegal = self.req.executor.call_tool(
                ToolCallRequest(
                    name="write_file",
                    arguments={"path": "out2.txt", "content": "illegal"},
                    meta={"claudecode/toolUseId": "call_w2", "message_id": "msg_2"},
                )
            )
            write_in_verify_result.append(res_illegal)
            # Calling report_verification in VERIFY must succeed and transition to RESPOND
            res_ver = self.req.executor.call_tool(
                ToolCallRequest(
                    name="report_verification",
                    arguments={"passed": True, "findings": "ok"},
                    meta={"claudecode/toolUseId": "call_rv", "message_id": "msg_2"},
                )
            )
            report_verify_result.append(res_ver)
            sink.on_text("Verificado com sucesso.")
            return NativeTurnOutcome(
                init=init,
                result=_make_result(text="Verificado com sucesso."),
                final_text="Verificado com sucesso.",
                exit_code=0,
            )

    class _VerifyProvider:
        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _VerifyHandle:
            return _VerifyHandle(req)

    rt = AgenticTurnRuntime(
        store=store,  # type: ignore[arg-type]
        provider=_VerifyProvider(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=200_000,
        ),
        toolset=ts,
        phase_controller=phases,
    )
    res = rt.run("t_friend")
    assert res.state == "completed"
    assert len(write_in_verify_result) == 1
    assert write_in_verify_result[0].is_error is True
    assert not (tmp_path / "out2.txt").exists()
    assert len(report_verify_result) == 1
    assert report_verify_result[0].is_error is False
    assert phases.current == Phase.RESPOND


def test_friend_optimistic_speak_retracted_when_read_succeeds_then_write_fails_in_same_message(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("x = 1\n", encoding="utf-8")
    store = _FriendStore(conversation_mode="friend")
    ws = ConversationWorkspace(tmp_path)
    ts = AgentToolset(ws, public_tool_names=False)

    class _RetractHandle:
        def __init__(self, req: NativeTurnRequest) -> None:
            self.req = req

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def send_user(self, content: Any) -> None:
            pass

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            init = _make_init()
            sink.on_init(init)
            # Same assistant message msg_1: speak + read_file (succeeds) + edit_file (fails)
            if hasattr(sink, "on_message_id"):
                sink.on_message_id("msg_1")
            sink.on_text("<speak>Pronto, já editei o arquivo para você!</speak>")
            r1 = self.req.executor.call_tool(
                ToolCallRequest(
                    name="read_file",
                    arguments={"path": "a.txt"},
                    meta={"claudecode/toolUseId": "c_read", "message_id": "msg_1"},
                )
            )
            assert r1.is_error is False
            r2 = self.req.executor.call_tool(
                ToolCallRequest(
                    name="edit_file",
                    arguments={"path": "a.txt", "old_string": "nao_existe", "new_string": "x = 2"},
                    meta={"claudecode/toolUseId": "c_edit", "message_id": "msg_1"},
                )
            )
            assert r2.is_error is True
            return NativeTurnOutcome(
                init=init,
                result=_make_result(text=""),
                final_text="",
                exit_code=0,
            )

    class _RetractProvider:
        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _RetractHandle:
            return _RetractHandle(req)

    rt = AgenticTurnRuntime(
        store=store,  # type: ignore[arg-type]
        provider=_RetractProvider(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=200_000,
        ),
        toolset=ts,
    )
    res = rt.run("t_friend")
    assert res.state == "completed"
    # Optimistic lie retraction must have been emitted as a spoken chunk
    assert any(
        etype == AgentActivityEventType.SPEAK_CHUNK_READY.value
        and payload.get("optimistic_lie_retraction") is True
        for etype, _summary, payload in store.speak_records
    )

