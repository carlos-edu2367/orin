from __future__ import annotations

import json
import logging
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
from typing import Any, Callable, Mapping

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.context_overflow import (
    format_context_overflow_message,
    parse_overflow_limit,
)
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.claude_cli.errors import (
    NativeLoopUnavailable,
    ProviderTransportError,
    error_info_to_transport_error,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnHandle,
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage, StreamJsonParser
from agentos.providers.cli_adapter import CliCapabilities, CliErrorKind, CliEventKind

FIXTURES = Path(__file__).resolve().parents[1] / "providers" / "claude_cli" / "fixtures"


def _make_init(model: str = "claude-fable-5-2") -> CliInit:
    return CliInit(
        session_id="sess_ok",
        model=model,
        api_key_source="none",
        mcp_servers={"orin": "connected"},
        tools=("mcp__orin__Read",),
    )


def _make_result(text: str = "Resposta normal apos compactacao.") -> CliResult:
    u = CliUsage(input_tokens=420, output_tokens=55)
    return CliResult(
        subtype="success",
        is_error=False,
        result_text=text,
        stop_reason="end_turn",
        num_turns=1,
        duration_ms=80,
        session_id="sess_ok",
        api_error_status=None,
        usage=u,
        model_usage={"claude-fable-5-2": u},
    )


class _OverflowStore:
    def __init__(self, history: list[dict[str, object]], *, model_id: str = "claude-fable-5-2") -> None:
        self._history = history
        self.turn: dict[str, object] = {
            "turn_id": "t_ovf",
            "conversation_id": "c_ovf",
            "user_id": "u1",
            "execution_id": "e_ovf",
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
        self.finished: list[dict[str, Any]] = []
        self.usages: list[dict[str, Any]] = []
        self._eff_seq = 0

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        return [dict(m) for m in self._history]

    def lifecycle(self, turn: Mapping[str, object], state: str, **payload: object) -> None:
        self.states.append((state, dict(payload)))

    def delta(self, turn: Mapping[str, object], text: str) -> None:
        self.deltas.append(text)

    def reset_assistant_message(self, turn: Mapping[str, object]) -> None:
        self.deltas.clear()

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
                "private_result": dict(private_result) if private_result else None,
            }
        )

    def record_usage(
        self,
        turn: Mapping[str, object],
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
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


class _ScriptedHandle:
    def __init__(
        self,
        req: NativeTurnRequest,
        handler: Callable[[NativeTurnRequest, NativeTurnSink, "_ScriptedHandle"], NativeTurnOutcome],
    ) -> None:
        self.req = req
        self._handler = handler
        self.closed = False
        self.synthetic_error_text: str | None = None

    def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
        return self._handler(self.req, sink, self)

    def refuse_tools(self, reason: str) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeCliProvider:
    def __init__(
        self,
        handlers: list[Callable[[NativeTurnRequest, NativeTurnSink, _ScriptedHandle], NativeTurnOutcome]],
        *,
        adapter_name: str = "claude",
    ) -> None:
        self._handlers = list(handlers)
        self.requests: list[NativeTurnRequest] = []
        self.adapter = SimpleNamespace(
            name=adapter_name,
            capabilities=CliCapabilities(
                native_tool_loop=True,
                tool_list_changed=True,
                images_in_tool_results=True,
                persistent_stdin=True,
            ),
        )

    def native_turn_available(self, turn: Mapping[str, object]) -> bool:
        return True

    def start_native_turn(self, req: NativeTurnRequest) -> _ScriptedHandle:
        self.requests.append(req)
        handler = self._handlers.pop(0)
        return _ScriptedHandle(req, handler)

    def stream_chat(self, req: Any) -> Any:
        # Used if runtime._request_compaction_summary calls provider.
        raise RuntimeError("fallback summary in unit test")


def _replay_fixture_handler(fixture_name: str) -> Callable[[NativeTurnRequest, NativeTurnSink, _ScriptedHandle], NativeTurnOutcome]:
    lines = (FIXTURES / fixture_name).read_text(encoding="utf-8").splitlines()
    adapter = ClaudeCliAdapter()

    def _run(_req: NativeTurnRequest, sink: NativeTurnSink, handle: _ScriptedHandle) -> NativeTurnOutcome:
        parser = StreamJsonParser()
        for line in lines:
            for ev in parser.feed_line(line):
                if ev.kind == CliEventKind.INIT and parser.init is not None:
                    sink.on_init(parser.init)
                elif ev.kind == CliEventKind.TEXT_DELTA and ev.text:
                    sink.on_text(ev.text)
                elif ev.kind == CliEventKind.USAGE and ev.usage is not None:
                    sink.on_assistant_usage(ev.message_id or "", ev.usage)
                elif ev.kind == CliEventKind.RESULT:
                    res = ev.result or parser.result
                    assert res is not None
                    if res.is_error:
                        handle.synthetic_error_text = parser.synthetic_error_text
                        err_info = adapter.classify_error(res, parser.rate_limit)
                        err = error_info_to_transport_error(err_info)
                        if parser.synthetic_error_text:
                            setattr(err, "synthetic_error_text", parser.synthetic_error_text)
                        raise err
        raise AssertionError("Fixture did not terminate with error result")

    return _run


def _normal_reply_handler(reply: str = "Resposta normal apos compactacao.") -> Callable[[NativeTurnRequest, NativeTurnSink, _ScriptedHandle], NativeTurnOutcome]:
    def _run(req: NativeTurnRequest, sink: NativeTurnSink, _handle: _ScriptedHandle) -> NativeTurnOutcome:
        init = _make_init(req.model or "claude-fable-5-2")
        sink.on_init(init)
        sink.on_text(reply)
        res = _make_result(reply)
        sink.on_assistant_usage("msg_ok_1", res.usage)
        return NativeTurnOutcome(result=res, init=init, final_text=reply, exit_code=0)

    return _run


def _large_history() -> list[dict[str, object]]:
    """Multi-turn history large enough to shrink noticeably when compacted, yet under 1M autocompact."""
    msgs: list[dict[str, object]] = []
    for i in range(6):
        msgs.append(
            {
                "role": "user",
                "content": f"Pergunta antiga {i}: " + ("detalhe de codigo " * 120),
                "_sequence": i * 2 + 1,
            }
        )
        msgs.append(
            {
                "role": "assistant",
                "content": f"Resposta antiga {i}: " + ("analise extensa " * 140),
                "_sequence": i * 2 + 2,
            }
        )
    msgs.append(
        {
            "role": "user",
            "content": "Agora conclua a tarefa final.",
            "_sequence": 13,
        }
    )
    return msgs


def _user_content_chars(req: NativeTurnRequest) -> int:
    return sum(len(str(b.get("text") or "")) for b in req.user_content if isinstance(b, Mapping))


def _make_runtime(
    tmp_path: Path,
    store: _OverflowStore,
    provider: _FakeCliProvider,
    *,
    autocompact: bool = True,
) -> AgenticTurnRuntime:
    workspace = ConversationWorkspace(tmp_path, "c_ovf")
    toolset = AgentToolset(workspace)
    return AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        sleep=lambda _s: None,
        autocompact=autocompact,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=2,
            context_window_tokens=1_000_000,
        ),
    )


def test_parse_overflow_limit_extracts_maximum_tokens() -> None:
    assert parse_overflow_limit("prompt is too long: 205123 tokens > 200000 maximum") == 200_000
    assert parse_overflow_limit("Prompt is too long: 210,500 tokens > 200,000 maximum") == 200_000
    assert parse_overflow_limit("1_005_000 token > 1_000_000 MAXIMUM") == 1_000_000
    assert parse_overflow_limit("Prompt is too long") is None
    assert parse_overflow_limit("0 tokens > 0 maximum") is None
    assert parse_overflow_limit("") is None


def test_format_context_overflow_message_pt_br() -> None:
    msg = format_context_overflow_message("Prompt is too long")
    assert "/compact" in msg
    assert "nova conversa" in msg.lower()
    assert "compact" in msg.lower()


def test_overflow_with_synthetic_marker_compacts_once_and_completes(tmp_path: Path) -> None:
    store = _OverflowStore(_large_history())
    provider = _FakeCliProvider(
        [
            _replay_fixture_handler("prompt-too-long.jsonl"),
            _normal_reply_handler("Tudo pronto apos compactar."),
        ]
    )
    runtime = _make_runtime(tmp_path, store, provider)
    res = runtime.run("t_ovf")

    assert res.state == "completed"
    assert len(provider.requests) == 2
    assert runtime._compaction_count == 1
    assert runtime._stream_resumes_used == 0
    assert not any(state == "stream_resuming" for state, _ in store.states)
    assert _user_content_chars(provider.requests[1]) < _user_content_chars(provider.requests[0])

    persisted_text = "".join(store.deltas)
    assert "Tudo pronto apos compactar." in persisted_text
    assert "Prompt is too long" not in persisted_text

    ovf_events = [
        payload
        for state, payload in store.states
        if state == AgentActivityEventType.CONTEXT_OVERFLOW_COMPACTED.value
    ]
    assert len(ovf_events) == 1
    assert ovf_events[0]["before_tokens"] > ovf_events[0]["after_tokens"]

    # E-RF15: zero-usage from error must never emit context_updated with used_tokens=0.
    zero_ctx_updates = [
        payload
        for state, payload in store.states
        if state == "context_updated" and payload.get("used_tokens") == 0
    ]
    assert zero_ctx_updates == []

    assert any(
        eff["state"] == "NOT_APPLIED" and eff["error_code"] == "CONTEXT_LENGTH_EXCEEDED_COMPACTING"
        for eff in store.effects_finished
    )


def test_overflow_twice_fails_with_context_length_exceeded_never_stream_interrupted(tmp_path: Path) -> None:
    store = _OverflowStore(_large_history())
    provider = _FakeCliProvider(
        [
            _replay_fixture_handler("prompt-too-long.jsonl"),
            _replay_fixture_handler("prompt-too-long.jsonl"),
        ]
    )
    runtime = _make_runtime(tmp_path, store, provider)
    res = runtime.run("t_ovf")

    assert res.state == "failed"
    assert res.error_code == "CONTEXT_LENGTH_EXCEEDED"
    assert len(provider.requests) == 2
    assert runtime._stream_resumes_used == 0
    assert not any(state == "stream_resuming" for state, _ in store.states)
    assert not any(
        eff.get("error_code") == "PROVIDER_STREAM_INTERRUPTED"
        for eff in store.effects_finished
    )
    failed_events = [p for s, p in store.states if s == "failed"]
    assert failed_events
    assert failed_events[-1].get("code") == "CONTEXT_LENGTH_EXCEEDED"
    msg = str(failed_events[-1].get("message") or store.turn.get("error_message") or "")
    assert "/compact" in msg
    assert "nova conversa" in msg.lower()


def test_overflow_without_synthetic_marker_discards_error_text_and_recovers(tmp_path: Path) -> None:
    store = _OverflowStore(_large_history())
    provider = _FakeCliProvider(
        [
            _replay_fixture_handler("prompt-too-long-no-marker.jsonl"),
            _normal_reply_handler("Resposta limpa sem texto de erro."),
        ]
    )
    runtime = _make_runtime(tmp_path, store, provider)
    res = runtime.run("t_ovf")

    assert res.state == "completed"
    assert len(provider.requests) == 2
    assert runtime._compaction_count == 1
    assert runtime._stream_resumes_used == 0
    assert not any(state == "stream_resuming" for state, _ in store.states)

    persisted_text = "".join(store.deltas)
    assert "Resposta limpa sem texto de erro." in persisted_text
    assert "Prompt is too long" not in persisted_text

    zero_ctx_updates = [
        payload
        for state, payload in store.states
        if state == "context_updated" and payload.get("used_tokens") == 0
    ]
    assert zero_ctx_updates == []


def test_overflow_compacts_even_when_autocompact_is_disabled(tmp_path: Path) -> None:
    store = _OverflowStore(_large_history())
    provider = _FakeCliProvider(
        [
            _replay_fixture_handler("prompt-too-long.jsonl"),
            _normal_reply_handler("Recuperado com autocompact desligado."),
        ]
    )
    runtime = _make_runtime(tmp_path, store, provider, autocompact=False)
    res = runtime.run("t_ovf")

    assert res.state == "completed"
    assert len(provider.requests) == 2
    assert runtime._compaction_count == 1
    assert not any(state == "stream_resuming" for state, _ in store.states)


@pytest.mark.parametrize(
    ("adapter_name", "exc_cls"),
    [
        ("codex", ProviderTransportError),
        ("antigravity", ProviderTransportError),
        ("claude", NativeLoopUnavailable),
    ],
)
def test_codex_antigravity_and_native_unavailable_overflow_recover_identically(
    tmp_path: Path,
    adapter_name: str,
    exc_cls: type[ProviderTransportError],
) -> None:
    store = _OverflowStore(_large_history())

    def _raise_overflow(_req: NativeTurnRequest, _sink: NativeTurnSink, _h: _ScriptedHandle) -> NativeTurnOutcome:
        raise exc_cls(
            "Model rejected request due to context size",
            status_code=400,
            error_kind=CliErrorKind.CONTEXT_OVERFLOW,
            end_reason="error_result",
        )

    provider = _FakeCliProvider(
        [
            _raise_overflow,
            _normal_reply_handler(f"OK via {adapter_name}"),
        ],
        adapter_name=adapter_name,
    )
    runtime = _make_runtime(tmp_path, store, provider)
    res = runtime.run("t_ovf")

    assert res.state == "completed"
    assert len(provider.requests) == 2
    assert runtime._compaction_count == 1
    assert not any(state == "stream_resuming" for state, _ in store.states)


def test_overflow_when_nothing_can_be_compacted_fails_cleanly(tmp_path: Path) -> None:
    # Single user message only — nothing prior can be aged or summarized.
    store = _OverflowStore([{"role": "user", "content": "Mensagem unica gigantesca", "_sequence": 1}])
    provider = _FakeCliProvider([_replay_fixture_handler("prompt-too-long.jsonl")])
    runtime = _make_runtime(tmp_path, store, provider)
    res = runtime.run("t_ovf")

    assert res.state == "failed"
    assert res.error_code == "CONTEXT_LENGTH_EXCEEDED"
    assert len(provider.requests) == 1
    assert not any(state == "stream_resuming" for state, _ in store.states)
    assert not any(
        eff.get("error_code") == "PROVIDER_STREAM_INTERRUPTED"
        for eff in store.effects_finished
    )


def test_native_loop_logs_sanitized_overflow_diagnostic_without_user_content(caplog: pytest.LogCaptureFixture) -> None:
    secret_user_text = "SEGREDO_CONFIDENCIAL_DO_USUARIO_12345"
    long_result = "Prompt is too long: " + ("X" * 3500)
    lines = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess_diag",
                "model": "claude-fable-5-2",
                "apiKeySource": "none",
                "mcp_servers": [{"name": "orin", "status": "connected"}],
                "tools": ["mcp__orin__Read"],
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": long_result,
                "api_error_status": 400,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "modelUsage": {"claude-fable-5-2": {"inputTokens": 0, "outputTokens": 0}},
                "user_prompt_echo": secret_user_text,
            }
        ),
    ]

    q: queue.Queue[str | None] = queue.Queue()
    for ln in lines:
        q.put(ln + "\n")
    q.put(None)

    class _FakeProc:
        def poll(self) -> int:
            return 0

    class _FakeBgServer:
        handle = SimpleNamespace(
            url="http://127.0.0.1:1/mcp",
            in_flight=0,
            refuse_calls=lambda _r: None,
            allow_calls=lambda: None,
        )

        def close(self, timeout: float = 1.0) -> None:
            pass

    class _NoopSink:
        def on_init(self, init: CliInit) -> None:
            pass

        def on_text(self, text: str) -> None:
            pass

        def on_thinking(self, text: str) -> None:
            pass

        def on_tool_use_started(self, tool_use_id: str, tool_name: str, message_id: str | None = None) -> None:
            pass

        def on_tool_input_progress(self, tool_use_id: str, tool_name: str, bytes_received: int) -> None:
            pass

        def on_tool_use_complete(self, tool_use_id: str, tool_name: str, tool_input: Mapping[str, Any], message_id: str | None = None) -> None:
            pass

        def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None:
            pass

        def on_compacted(self) -> None:
            pass

        def on_rate_limit(self, state: Any) -> None:
            pass

    noop_thread = threading.Thread(target=lambda: None)
    handle = NativeTurnHandle(
        proc=_FakeProc(),
        cmd=["claude"],
        requested_model="claude-fable-5-2",
        server_name="orin",
        bg_server=_FakeBgServer(),  # type: ignore[arg-type]
        temp_files=[],
        slots=SimpleNamespace(release=lambda: None),  # type: ignore[arg-type]
        cancel_check=lambda: False,
        deadline_monotonic=1e12,
        clock=lambda: 0.0,
        kill_tree_fn=lambda _p: None,
        log_run_fn=lambda **_kw: None,
        stdout_queue=q,
        stderr_chunks=[],
        stdout_thread=noop_thread,
        stderr_thread=noop_thread,
        t0=0.0,
        adapter=ClaudeCliAdapter(),
        first_message_bytes=198_432,
    )

    with caplog.at_level(logging.WARNING, logger="agentos.providers.claude_cli.native_loop"):
        with pytest.raises(ProviderTransportError) as exc_info:
            handle.run_until_result(_NoopSink())

    assert exc_info.value.error_kind == CliErrorKind.CONTEXT_OVERFLOW
    warning_messages = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert warning_messages
    diag_msg = warning_messages[-1]
    assert "198432" in diag_msg
    assert "error_during_execution" in diag_msg
    assert "modelUsage" in diag_msg
    assert "api_error_status" in diag_msg
    assert secret_user_text not in diag_msg
    # The raw result text inside the diagnostic must be truncated to <= 2 KB.
    assert len(diag_msg) < 2800


def _rehydrated_190k_history() -> list[dict[str, object]]:
    """Rehydrated history of ~190k estimated tokens (~760k chars across multiple turns + tool results)."""
    msgs: list[dict[str, object]] = []
    # 10 turns * ~76k chars (~19k tokens each) = ~190k tokens
    chunk_user = "Contexto detalhado do projeto " + ("codigo_modulo_alpha " * 1400)  # ~28k chars
    chunk_assistant = "Analise tecnica extensa " + ("implementacao_beta " * 2500)  # ~47.5k chars
    seq = 1
    for i in range(10):
        msgs.append({"role": "user", "content": f"Turno {i}: {chunk_user}", "_sequence": seq})
        seq += 1
        msgs.append({"role": "assistant", "content": f"Resposta {i}: {chunk_assistant}", "_sequence": seq})
        seq += 1
    msgs.append({"role": "user", "content": "Prossiga com o ultimo ajuste.", "_sequence": seq})
    return msgs


def test_fable_52_rehydrated_190k_history_with_effective_200k_compacts_before_spawn_and_sets_autocompact_200000(
    tmp_path: Path,
) -> None:
    from agentos.providers.claude_cli.command import build_claude_command
    from agentos.providers.claude_cli.effective_window import effective_window_for
    from agentos.quota.claude_account import ClaudeAccountReader

    account_state = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json").read()
    ew = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=account_state,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert ew.tokens == 200_000
    assert ew.source == "account_credits"
    assert ew.rule_index == 3

    store = _OverflowStore(_rehydrated_190k_history(), model_id="claude-fable-5-2")
    provider = _FakeCliProvider([_normal_reply_handler("Compactado antes de abrir o CLI.")])
    workspace = ConversationWorkspace(tmp_path, "c_fable_pre")
    toolset = AgentToolset(workspace)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        sleep=lambda _s: None,
        autocompact=True,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=1,
            max_context_tokens=180_000,
            context_window_tokens=ew.tokens,
        ),
        window_source=ew.source,
        window_detail=ew.detail,
        window_rule_index=ew.rule_index,
        cli_model_arg=ew.cli_model_arg,
    )
    res = runtime.run("t_ovf")

    assert res.state == "completed"
    assert len(provider.requests) == 1
    assert runtime._compaction_count >= 1
    first_msg_tokens = _user_content_chars(provider.requests[0]) // 4
    assert first_msg_tokens < 170_000
    assert provider.requests[0].autocompact == 200_000

    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("sys", encoding="utf-8")
    cmd = build_claude_command(
        binary="claude",
        system_prompt_file=prompt_file,
        model=provider.requests[0].model,
        autocompact=provider.requests[0].autocompact,
    )
    idx = cmd.index("--autocompact")
    assert cmd[idx + 1] == "200000"

    ctx_events = [p for s, p in store.states if s == "context_updated"]
    assert ctx_events
    assert ctx_events[0].get("window_source") == "account_credits"
    assert "200 mil" in str(ctx_events[0].get("window_detail") or "")


def test_fable_52_without_fixture_learns_200k_from_raw_overflow_and_next_turn_compacts_before_spawn(
    tmp_path: Path,
) -> None:
    from sqlalchemy import create_engine

    from agentos.persistence.postgres.context_observations import ContextObservationStore
    from agentos.persistence.postgres.migrate import upgrade
    from agentos.providers.claude_cli.effective_window import effective_window_for

    engine = create_engine(f"sqlite:///{(tmp_path / 'fable_learn.db').as_posix()}")
    upgrade(engine, "head")
    try:
        obs_store = ContextObservationStore(engine)

        # Turn 1: no ~/.claude.json fixture -> CLI catalog says 1M
        ew1 = effective_window_for(
            "anthropic",
            "claude-fable-5-2",
            account_state=None,
            cli_catalog_window=1_000_000,
            fixed_catalog_window=200_000,
        )
        assert ew1.tokens == 1_000_000

        def _raw_overflow_handler(_req: NativeTurnRequest, _sink: NativeTurnSink, _handle: _ScriptedHandle) -> NativeTurnOutcome:
            raise ProviderTransportError(
                "Prompt is too long: 205123 tokens > 200000 maximum",
                status_code=400,
                end_reason="error_result",
                error_kind=CliErrorKind.CONTEXT_OVERFLOW,
            )

        store1 = _OverflowStore(_large_history(), model_id="claude-fable-5-2")
        provider1 = _FakeCliProvider([_raw_overflow_handler, _normal_reply_handler("Recuperado apos aprender.")])
        workspace1 = ConversationWorkspace(tmp_path, "c_fable_t1")
        runtime1 = AgenticTurnRuntime(
            store=store1,
            provider=provider1,
            toolset=AgentToolset(workspace1),
            sleep=lambda _s: None,
            autocompact=True,
            limits=AgenticLimits(
                max_iterations=10,
                max_actions=10,
                max_provider_retries=1,
                max_context_tokens=900_000,
                context_window_tokens=ew1.tokens,
            ),
            window_source=ew1.source,
            window_detail=ew1.detail,
            window_rule_index=ew1.rule_index,
            context_observation_sink=lambda prov, mdl, lim, src, first_message_tokens=None: obs_store.learn(
                "u1",
                prov,
                mdl,
                lim,
                src,
                first_message_tokens=first_message_tokens,
            ),
        )
        res1 = runtime1.run("t_ovf")
        assert res1.state == "completed"
        assert len(provider1.requests) == 2

        learned = obs_store.get("u1", "anthropic", "claude-fable-5-2")
        assert learned is not None
        assert learned.observed_limit_tokens == 200_000
        assert learned.source == "error_raw"

        learned_events = [
            p for s, p in store1.states if s == AgentActivityEventType.CONTEXT_WINDOW_LEARNED.value
        ]
        assert len(learned_events) == 1
        assert learned_events[0]["model"] == "claude-fable-5-2"
        assert learned_events[0]["tokens"] == 200_000
        assert learned_events[0]["source"] == "error_raw"

        # Turn 2: learned observation wins -> effective window is 200k -> compacts before spawning CLI!
        ew2 = effective_window_for(
            "anthropic",
            "claude-fable-5-2",
            learned_limit=learned.observed_limit_tokens,
            learned_source=learned.source,
            account_state=None,
            cli_catalog_window=1_000_000,
            fixed_catalog_window=200_000,
        )
        assert ew2.tokens == 200_000
        assert ew2.source == "learned"
        assert ew2.rule_index == 2

        store2 = _OverflowStore(_rehydrated_190k_history(), model_id="claude-fable-5-2")
        provider2 = _FakeCliProvider([_normal_reply_handler("Turno 2 sem estouro.")])
        workspace2 = ConversationWorkspace(tmp_path, "c_fable_t2")
        runtime2 = AgenticTurnRuntime(
            store=store2,
            provider=provider2,
            toolset=AgentToolset(workspace2),
            sleep=lambda _s: None,
            autocompact=True,
            limits=AgenticLimits(
                max_iterations=10,
                max_actions=10,
                max_provider_retries=1,
                max_context_tokens=180_000,
                context_window_tokens=ew2.tokens,
            ),
            window_source=ew2.source,
            window_detail=ew2.detail,
            window_rule_index=ew2.rule_index,
            learned_window_tokens=learned.observed_limit_tokens,
        )
        res2 = runtime2.run("t_ovf")
        assert res2.state == "completed"
        assert len(provider2.requests) == 1
        assert runtime2._compaction_count >= 1
        assert _user_content_chars(provider2.requests[0]) // 4 < 170_000
        assert provider2.requests[0].autocompact == 200_000
    finally:
        engine.dispose()


def test_successful_turn_exceeding_learned_window_invalidates_observation(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    from agentos.persistence.postgres.context_observations import ContextObservationStore
    from agentos.persistence.postgres.migrate import upgrade

    engine = create_engine(f"sqlite:///{(tmp_path / 'inv_ok.db').as_posix()}")
    upgrade(engine, "head")
    try:
        obs_store = ContextObservationStore(engine)
        obs_store.learn("u1", "anthropic", "claude-fable-5-2", 150_000, "estimate")
        assert obs_store.get("u1", "anthropic", "claude-fable-5-2") is not None

        def _high_usage_handler(req: NativeTurnRequest, sink: NativeTurnSink, _handle: _ScriptedHandle) -> NativeTurnOutcome:
            init = _make_init(req.model or "claude-fable-5-2")
            sink.on_init(init)
            sink.on_text("Resposta com uso maior que o limite aprendido.")
            u = CliUsage(input_tokens=180_000, output_tokens=100, context_window_used_tokens=180_000)
            sink.on_assistant_usage("msg_big", u)
            res = CliResult(
                subtype="success",
                is_error=False,
                result_text="Resposta com uso maior que o limite aprendido.",
                stop_reason="end_turn",
                num_turns=1,
                duration_ms=80,
                session_id="sess_ok",
                api_error_status=None,
                usage=u,
                model_usage={"claude-fable-5-2": u},
            )
            return NativeTurnOutcome(result=res, init=init, final_text=res.result_text, exit_code=0)

        store = _OverflowStore([{"role": "user", "content": "Ola", "_sequence": 1}], model_id="claude-fable-5-2")
        provider = _FakeCliProvider([_high_usage_handler])
        runtime = AgenticTurnRuntime(
            store=store,
            provider=provider,
            toolset=AgentToolset(ConversationWorkspace(tmp_path, "c_inv")),
            sleep=lambda _s: None,
            limits=AgenticLimits(
                max_iterations=5,
                max_actions=5,
                max_context_tokens=150_000,
                context_window_tokens=150_000,
            ),
            learned_window_tokens=150_000,
            context_observation_invalidator=lambda prov, mdl: obs_store.invalidate("u1", prov, mdl),
        )
        res = runtime.run("t_ovf")
        assert res.state == "completed"
        assert obs_store.get("u1", "anthropic", "claude-fable-5-2") is None
    finally:
        engine.dispose()


def test_long_context_1m_credits_error_falls_back_to_200k_without_1m_suffix(tmp_path: Path) -> None:
    def _credits_required_handler(_req: NativeTurnRequest, _sink: NativeTurnSink, _handle: _ScriptedHandle) -> NativeTurnOutcome:
        raise ProviderTransportError(
            "Usage credits required for 1M context",
            status_code=400,
            end_reason="error_result",
            error_kind=CliErrorKind.FATAL,
        )

    store = _OverflowStore(_large_history(), model_id="claude-fable-5-2")
    provider = _FakeCliProvider([_credits_required_handler, _normal_reply_handler("Fallback 200k concluido.")])
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "c_1m_fb")),
        sleep=lambda _s: None,
        autocompact=True,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=1,
            max_context_tokens=900_000,
            context_window_tokens=1_000_000,
        ),
        window_source="credits_1m",
        window_detail="1 M, créditos de uso ativos (1M)",
        window_rule_index=3,
        cli_model_arg="claude-fable-5-2[1m]",
    )
    res = runtime.run("t_ovf")
    assert res.state == "completed"
    assert len(provider.requests) == 2
    assert provider.requests[0].model == "claude-fable-5-2[1m]"
    assert provider.requests[1].model == "claude-fable-5-2"
    assert provider.requests[1].autocompact == 200_000
    assert runtime.limits.context_window_tokens == 200_000
    assert runtime.window_source == "account_credits"
    ctx_updates = [p for s, p in store.states if s == "context_updated"]
    assert any(p.get("limit_tokens") == 200_000 and p.get("window_source") == "account_credits" for p in ctx_updates)


def test_agy_max_message_tokens_error_triggers_reactive_compaction_on_system_history(
    tmp_path: Path,
) -> None:
    """agy 1.5.x reports 'maxMessageTokens must be positive, got 0' when the system prompt
    (with prior history) fills the model window. Reactive compaction must shrink prior_history
    and retry successfully.
    """
    def _agy_overflow_handler(
        _req: NativeTurnRequest,
        _sink: NativeTurnSink,
        _handle: _ScriptedHandle,
    ) -> NativeTurnOutcome:
        raise ProviderTransportError(
            "agent executor error: generating and executing: serializing trajectory: maxMessageTokens must be positive, got 0",
            status_code=None,
            end_reason="error_result",
            error_kind=CliErrorKind.FATAL,
        )

    store = _OverflowStore(_large_history(), model_id="gemini-4-neon-high")
    store.turn["provider"] = "google-antigravity"
    provider = _FakeCliProvider([_agy_overflow_handler, _normal_reply_handler("Recuperado apos compactacao agy.")])
    provider.adapter = SimpleNamespace(
        name="antigravity",
        capabilities=CliCapabilities(
            native_tool_loop=True,
            tool_list_changed=True,
            persistent_stdin=True,
            max_user_message_bytes=190_791,
            history_in_system_prompt=True,
        ),
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "c_agy_ovf")),
        sleep=lambda _s: None,
        autocompact=True,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=1,
            max_context_tokens=256_000,
            context_window_tokens=256_000,
        ),
    )
    res = runtime.run("t_ovf")
    assert res.state == "completed"
    assert runtime._compaction_count == 1
    assert len(provider.requests) == 2
    assert len(provider.requests[1].prior_history) < len(provider.requests[0].prior_history)
    assert not any(state == "stream_resuming" for state, _ in store.states)


def test_agy_max_message_tokens_overflow_with_small_estimate_does_not_poison_learned_limit(
    tmp_path: Path,
) -> None:
    """M2: 'maxMessageTokens must be positive, got 0' has no explicit token limit. When the estimated
    first_n is far below the catalog window (e.g. ~25k vs 256k), reactive compaction must still recover
    the turn without persisting a poisoned low learned_limit or shrinking the runtime window.
    """
    def _agy_overflow_handler(
        _req: NativeTurnRequest,
        _sink: NativeTurnSink,
        _handle: _ScriptedHandle,
    ) -> NativeTurnOutcome:
        raise ProviderTransportError(
            "agent executor error: generating and executing: serializing trajectory: maxMessageTokens must be positive, got 0",
            status_code=400,
            end_reason="error_result",
            error_kind=CliErrorKind.CONTEXT_OVERFLOW,
        )

    store = _OverflowStore(_large_history(), model_id="gemini-4-neon-high")
    store.turn["provider"] = "google-antigravity"
    provider = _FakeCliProvider([_agy_overflow_handler, _normal_reply_handler("Recuperado sem envenenar janela.")])
    provider.adapter = SimpleNamespace(
        name="antigravity",
        capabilities=CliCapabilities(
            native_tool_loop=True,
            tool_list_changed=True,
            persistent_stdin=True,
            max_user_message_bytes=190_791,
            history_in_system_prompt=True,
        ),
    )
    learned_calls: list[tuple[Any, ...]] = []

    def _obs_sink(prov: str, mdl: str, lim: int, src: str, first_message_tokens: int | None = None) -> None:
        learned_calls.append((prov, mdl, lim, src, first_message_tokens))

    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "c_agy_m2")),
        sleep=lambda _s: None,
        autocompact=True,
        limits=AgenticLimits(
            max_iterations=10,
            max_actions=10,
            max_provider_retries=1,
            max_context_tokens=256_000,
            context_window_tokens=256_000,
        ),
        context_observation_sink=_obs_sink,
    )
    res = runtime.run("t_ovf")
    assert res.state == "completed"
    assert runtime._compaction_count == 1
    assert len(provider.requests) == 2
    assert learned_calls == []
    assert runtime.limits.context_window_tokens == 256_000
    assert runtime.learned_window_tokens is None
    assert not any(
        state == AgentActivityEventType.CONTEXT_WINDOW_LEARNED.value
        for state, _ in store.states
    )


def test_is_context_overflow_error_detects_max_message_tokens_in_output_tail() -> None:
    """M4: When agy prints the error only to stderr and exits without a result event,
    is_context_overflow_error must still detect it via exc.output_tail.
    """
    from agentos.agentic.context_overflow import is_context_overflow_error

    exc = ProviderTransportError(
        "CLI encerrou sem evento result",
        status_code=502,
        end_reason="eof_without_result",
        error_kind=CliErrorKind.FATAL,
        output_tail=(
            "stderr: agent executor error: generating and executing: "
            "serializing trajectory: maxMessageTokens must be positive, got 0"
        ),
    )
    assert is_context_overflow_error(exc) is True



