from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import pytest

from agentos.providers.claude_cli.errors import CliSecurityAbort
from agentos.providers.claude_cli.native_loop import (
    NativeTurnRequest,
    NativeTurnSink,
    start_native_turn_for_provider,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from agentos.providers.cli_adapter import (
    CliErrorKind,
    CliEventKind,
    CliInit,
    CliResult,
    CliTurnRequest,
    CliUsage,
    RateLimitState,
)
from agentos.providers.cli_registry import CLI_PROVIDERS, cli_adapter_for
from agentos.providers.cli_tools_mcp import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
)
from agentos.providers.codex_cli import CodexCliAdapter, classify_codex_error
from tests.unit.providers.cli_adapter_conformance import (
    AdapterFixtures,
    CliAdapterConformance,
    ErrorScenario,
    StreamScenario,
    load_jsonl,
)

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CODEX_SCRIPT = str(Path(__file__).with_name("fake_codex.py"))


def _make_error_result(
    text: str,
    *,
    api_error_status: int | None = None,
) -> CliResult:
    return CliResult(
        subtype="error",
        is_error=True,
        result_text=text,
        stop_reason="error",
        num_turns=1,
        duration_ms=10,
        session_id="thread-err-1",
        api_error_status=api_error_status,
        usage=CliUsage(),
        model_usage={},
    )


class TestCodexCliAdapterConformance(CliAdapterConformance):
    def test_encode_user_turn_is_single_line_utf8(self) -> None:
        # Codex reads stdin until EOF, so the line breaks stay (headings and role markers need them).
        encoded = self.get_fixtures().adapter.encode_user_turn([{"type": "text", "text": "olá\nmundo"}])
        assert encoded.decode("utf-8") == "olá\nmundo\n"

    def adapter_fixtures(self) -> AdapterFixtures:
        return AdapterFixtures(
            adapter=CodexCliAdapter(),
            simple_text=StreamScenario(
                lines=load_jsonl(FIXTURES / "simple_text.jsonl"),
                expected_text="PONG_CODEX",
                expected_session_id="01a1b331-e989-7f71-ae46-8e13f3d2e46f",
            ),
            tool_call=StreamScenario(
                lines=load_jsonl(FIXTURES / "tool_call.jsonl"),
                expected_text="ECHO_OK:hello-from-codex",
                expected_tool_calls=(("echo", {"text": "hello-from-codex"}),),
                expected_session_id="01a1b332-0328-7b73-848c-bad78a321689",
            ),
            error_scenarios=(
                ErrorScenario(
                    lines=load_jsonl(FIXTURES / "auth_error.jsonl"),
                    expected_kind=CliErrorKind.AUTH,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=[
                        '{"type":"thread.started","thread_id":"th-rl"}',
                        '{"type":"turn.started"}',
                        '{"type":"turn.failed","error":{"message":"You have hit your limit. Try again in 30s."}}',
                    ],
                    expected_kind=CliErrorKind.RATE_LIMITED,
                    expected_retryable=True,
                ),
                ErrorScenario(
                    lines=load_jsonl(FIXTURES / "bad_model.jsonl"),
                    expected_kind=CliErrorKind.MODEL_NOT_FOUND,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=[
                        '{"type":"thread.started","thread_id":"th-ctx"}',
                        '{"type":"turn.started"}',
                        '{"type":"turn.failed","error":{"message":"context_length_exceeded: prompt is too long for context window"}}',
                    ],
                    expected_kind=CliErrorKind.CONTEXT_OVERFLOW,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=[
                        '{"type":"thread.started","thread_id":"th-503"}',
                        '{"type":"turn.started"}',
                        '{"type":"turn.failed","error":{"message":"503 Service Unavailable: upstream connection reset"}}',
                    ],
                    expected_kind=CliErrorKind.TRANSIENT,
                    expected_retryable=True,
                ),
            ),
        )


@pytest.mark.parametrize(
    "msg,api_status",
    [
        ("unexpected status 401 Unauthorized: Missing authentication in header", None),
        ("Missing bearer token in request", None),
        ("User is not logged in. Please run codex login first.", None),
        ("Authentication failed for current session", None),
        ("Invalid token provided", None),
        ("OAuth session expired", None),
        ("Access denied", 401),
    ],
)
def test_classify_codex_error_auth(msg: str, api_status: int | None) -> None:
    info = classify_codex_error(
        _make_error_result(msg, api_error_status=api_status)
    )
    assert info.kind == CliErrorKind.AUTH
    assert info.retryable is False
    assert info.status_code == 401
    assert "Faça login com `codex login`" in info.user_message


@pytest.mark.parametrize(
    "msg,expected_retry_after",
    [
        ("Rate limit reached. Try again in 30s.", 30.0),
        ("Usage limit hit, please try again in 15m", 900.0),
        ("You have hit your limit. Try again in 2h.", 7200.0),
        ("Too many requests, retry in 45 seconds", 45.0),
        ("Quota exceeded, resets in 10 minutes", 600.0),
        ("429 Too Many Requests: usage limit reached, resets at 18:00", None),
    ],
)
def test_classify_codex_error_rate_limited_messages_and_durations(
    msg: str, expected_retry_after: float | None
) -> None:
    info = classify_codex_error(_make_error_result(msg))
    assert info.kind == CliErrorKind.RATE_LIMITED
    assert info.retryable is True
    assert info.status_code == 429
    if expected_retry_after is None:
        assert info.retry_after_seconds is None
    else:
        assert info.retry_after_seconds == pytest.approx(expected_retry_after)


@pytest.mark.parametrize("rl_status", ["rejected", "exhausted", "rate_limited"])
def test_classify_codex_error_rate_limited_from_state_and_resets_at(
    rl_status: str,
) -> None:
    now_ts = time.time()
    rl = RateLimitState(
        status=rl_status, rate_limit_type="five_hour", resets_at=now_ts + 120.0
    )
    info = classify_codex_error(_make_error_result("generic failure"), rl)
    assert info.kind == CliErrorKind.RATE_LIMITED
    assert info.retryable is True
    assert info.status_code == 429
    assert info.retry_after_seconds is not None
    assert 60.0 <= info.retry_after_seconds <= 130.0


@pytest.mark.parametrize(
    "msg,api_status",
    [
        (
            "The 'gpt-nonexistent-model-9999' model is not supported when using Codex with a ChatGPT account.",
            None,
        ),
        ("Model not found: gpt-99", None),
        ("The model `gpt-unknown` does not exist", None),
        ("Unknown model requested", None),
        ("Invalid model identifier", None),
        ("Model metadata for `gpt-foo` not found.", None),
        ("404 Not Found", 404),
    ],
)
def test_classify_codex_error_model_not_found(
    msg: str, api_status: int | None
) -> None:
    info = classify_codex_error(
        _make_error_result(msg, api_error_status=api_status)
    )
    assert info.kind == CliErrorKind.MODEL_NOT_FOUND
    assert info.retryable is False
    assert info.status_code == 404


@pytest.mark.parametrize(
    "msg",
    [
        "context_length_exceeded: input exceeds limit",
        "Request exceeds the context window of this model",
        "This model's maximum context length is 272000 tokens",
        "Prompt is too long (310000 tokens > 272000)",
        "Too many tokens in request",
    ],
)
def test_classify_codex_error_context_overflow(msg: str) -> None:
    info = classify_codex_error(_make_error_result(msg))
    assert info.kind == CliErrorKind.CONTEXT_OVERFLOW
    assert info.retryable is False
    assert info.status_code == 400


@pytest.mark.parametrize(
    "msg,api_status,expected_status",
    [
        ("500 Internal Server Error", None, 503),
        ("502 Bad Gateway", None, 503),
        ("503 Service Unavailable", None, 503),
        ("504 Gateway Timeout", None, 503),
        ("529 Overloaded", 529, 529),
        ("Connection reset by peer", None, 503),
        ("Connection refused", None, 503),
        ("Request timed out while waiting for stream", None, 503),
        ("Reconnecting... 5/5 (stream dropped)", None, 503),
        ("Server is currently overloaded", 502, 502),
    ],
)
def test_classify_codex_error_transient(
    msg: str, api_status: int | None, expected_status: int
) -> None:
    info = classify_codex_error(
        _make_error_result(msg, api_error_status=api_status)
    )
    assert info.kind == CliErrorKind.TRANSIENT
    assert info.retryable is True
    assert info.status_code == expected_status


def test_classify_codex_error_fatal_fallback() -> None:
    info = classify_codex_error(
        _make_error_result("Unexpected internal panic in codex binary")
    )
    assert info.kind == CliErrorKind.FATAL
    assert info.retryable is False
    assert info.status_code == 502


def test_partial_text_preserved_before_turn_failed_and_error() -> None:
    adapter = CodexCliAdapter()
    tr = adapter.stream_translator()
    lines = [
        '{"type":"thread.started","thread_id":"th-partial-1"}',
        '{"type":"turn.started"}',
        '{"type":"item.updated","item":{"id":"msg_1","type":"agent_message","text":"Partial response before "}}',
        '{"type":"item.completed","item":{"id":"msg_1","type":"agent_message","text":"Partial response before failure"}}',
        '{"type":"turn.failed","error":{"message":"503 Service Unavailable: upstream dropped"}}',
    ]
    events = []
    for ln in lines:
        events.extend(tr.feed_line(ln))
    events.extend(tr.flush_pending())

    emitted_text = "".join(
        ev.text for ev in events if ev.kind == CliEventKind.TEXT_DELTA
    )
    assert emitted_text == "Partial response before failure"
    assert tr.result is not None
    assert tr.result.is_error is True
    err_info = adapter.classify_error(tr.result, tr.rate_limit)
    assert err_info.kind == CliErrorKind.TRANSIENT
    assert err_info.retryable is True


def test_cli_registry_registers_openai_codex() -> None:
    assert "openai-codex" in CLI_PROVIDERS
    adapter = cli_adapter_for("openai-codex", None)
    assert isinstance(adapter, CodexCliAdapter)
    assert adapter.name == "codex"


def test_adapter_build_command_filters_effort_using_discovered_models(
    tmp_path: Path,
) -> None:
    adapter = CodexCliAdapter()
    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("System instructions", encoding="utf-8")

    # gpt-6-luna supports ("low", "medium", "high", "xhigh", "max"), NOT "ultra"
    req_unsupported = CliTurnRequest(
        binary="codex",
        system_prompt_file=sys_file,
        model="gpt-6-luna",
        effort="ultra",
    )
    argv_unsupported = adapter.build_command(req_unsupported)
    assert not any("model_reasoning_effort" in arg for arg in argv_unsupported)

    # gpt-6.1-sol supports "ultra"
    req_supported = CliTurnRequest(
        binary="codex",
        system_prompt_file=sys_file,
        model="gpt-6.1-sol",
        effort="ultra",
    )
    argv_supported = adapter.build_command(req_supported)
    assert 'model_reasoning_effort="ultra"' in argv_supported


class _RecordingSink(NativeTurnSink):
    def __init__(self) -> None:
        self.inits: list[CliInit] = []
        self.texts: list[str] = []
        self.tools_started: list[tuple[str, str]] = []
        self.tools_complete: list[tuple[str, str, Mapping[str, Any]]] = []
        self.usages: list[tuple[str, CliUsage]] = []

    def on_init(self, init: CliInit) -> None:
        self.inits.append(init)

    def on_text(self, text: str) -> None:
        self.texts.append(text)

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_use_started(self, tool_use_id: str, tool_name: str) -> None:
        self.tools_started.append((tool_use_id, tool_name))

    def on_tool_input_progress(
        self, tool_use_id: str, tool_name: str, bytes_received: int
    ) -> None:
        pass

    def on_tool_use_complete(
        self, tool_use_id: str, tool_name: str, tool_input: Mapping[str, Any]
    ) -> None:
        self.tools_complete.append((tool_use_id, tool_name, dict(tool_input)))

    def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None:
        self.usages.append((message_id, usage))

    def on_compacted(self) -> None:
        pass

    def on_rate_limit(self, state: RateLimitState) -> None:
        pass


class _EchoExecutor:
    def __init__(self) -> None:
        self.calls: list[ToolCallRequest] = []

    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="Echo text back",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                read_only=True,
            )
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        return ToolCallResult(text=f"ECHO_OK:{request.arguments.get('text')}")


def test_e2e_two_turns_with_fake_codex_and_turn_tool_server(
    tmp_path: Path,
) -> None:
    adapter = CodexCliAdapter()
    executor = _EchoExecutor()
    provider = ClaudeCliProvider(
        binary=FAKE_CODEX_SCRIPT,
        adapter=adapter,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()

    # Turn 1: initial turn without resume_session_id
    req1 = NativeTurnRequest(
        model="gpt-6.1-sol",
        system_prompt="You are a Codex test agent.",
        user_content=[{"type": "text", "text": "Hello from turn 1"}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle1 = start_native_turn_for_provider(provider, req1)
    try:
        sink1 = _RecordingSink()
        out1 = handle1.run_until_result(sink1)
    finally:
        handle1.close()

    assert out1.init is not None
    assert out1.init.session_id == "thread-e2e-1"
    assert len(executor.calls) == 1
    assert (
        executor.calls[0].arguments["text"]
        == "mode=new|thread=thread-e2e-1|prompt=Hello from turn 1"
    )
    assert (
        out1.final_text
        == "Codex[thread-e2e-1]:ECHO_OK:mode=new|thread=thread-e2e-1|prompt=Hello from turn 1"
    )
    assert sink1.tools_started == [("item_0", "echo")]
    assert sink1.tools_complete == [
        ("item_0", "echo", {"text": "Hello from turn 1"})
    ]

    # Turn 2: resumed turn with resume_session_id="thread-e2e-1"
    req2 = NativeTurnRequest(
        model="gpt-6.1-sol",
        system_prompt="You are a Codex test agent.",
        user_content=[{"type": "text", "text": "Follow up turn 2"}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
        resume_session_id=out1.init.session_id,
    )
    handle2 = start_native_turn_for_provider(provider, req2)
    try:
        assert handle2._cmd[1:4] == ["exec", "resume", "thread-e2e-1"]
        sink2 = _RecordingSink()
        out2 = handle2.run_until_result(sink2)
    finally:
        handle2.close()

    assert out2.init is not None
    assert out2.init.session_id == "thread-e2e-1"
    assert len(executor.calls) == 2
    assert (
        executor.calls[1].arguments["text"]
        == "mode=resume|thread=thread-e2e-1|prompt=Follow up turn 2"
    )
    assert (
        out2.final_text
        == "Codex[thread-e2e-1]:ECHO_OK:mode=resume|thread=thread-e2e-1|prompt=Follow up turn 2"
    )


def test_e2e_fake_codex_forbidden_item_aborts_with_cli_security_abort(
    tmp_path: Path,
) -> None:
    adapter = CodexCliAdapter()
    executor = _EchoExecutor()
    provider = ClaudeCliProvider(
        binary=FAKE_CODEX_SCRIPT,
        adapter=adapter,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()

    req = NativeTurnRequest(
        model="gpt-6.1-sol",
        system_prompt="You are a Codex test agent.",
        user_content=[{"type": "text", "text": "Run FORBIDDEN_SHELL now"}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = start_native_turn_for_provider(provider, req)
    try:
        sink = _RecordingSink()
        with pytest.raises(CliSecurityAbort) as exc_info:
            handle.run_until_result(sink)
        assert "command_execution" in str(exc_info.value)
        assert executor.calls == []
    finally:
        handle.close()


def test_adversarial_error_classification_edge_cases() -> None:
    # 1. Rate-limit message containing the word "expired" should still classify as RATE_LIMITED
    rl_expired = classify_codex_error(
        _make_error_result("Quota window expired, please try again in 15m")
    )
    assert rl_expired.kind == CliErrorKind.RATE_LIMITED
    assert rl_expired.retryable is True
    assert rl_expired.retry_after_seconds == pytest.approx(900.0)

    # 2. Non-finite or non-positive resets_at in RateLimitState must not leak nan/inf/negative
    for bad_reset in (float("nan"), float("inf"), float("-inf"), -50.0, 0.0):
        info = classify_codex_error(
            _make_error_result("rate limit reached"),
            RateLimitState(status="rejected", resets_at=bad_reset),
        )
        assert info.kind == CliErrorKind.RATE_LIMITED
        assert info.retry_after_seconds is None

    # 3. Empty/whitespace result_text and boolean api_error_status
    empty_info = classify_codex_error(
        _make_error_result("   ", api_error_status=True)  # type: ignore[arg-type]
    )
    assert empty_info.kind == CliErrorKind.FATAL
    assert empty_info.status_code == 502
    assert empty_info.user_message == "Codex CLI execution failed"

    # 4. Login hint is not duplicated if already present in message
    already_hinted = classify_codex_error(
        _make_error_result(
            "401 Unauthorized — Faça login com `codex login`"
        )
    )
    assert already_hinted.kind == CliErrorKind.AUTH
    assert (
        already_hinted.user_message.count("Faça login com `codex login`") == 1
    )


def test_adapter_resolve_binary_and_version_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = CodexCliAdapter()
    assert adapter.version_supported((0, 167, 0)) is True
    assert adapter.version_supported((0, 166, 9)) is False
    assert adapter.version_supported((1, 0, 0)) is False
    assert adapter.version_supported(None) is False

    monkeypatch.setattr(
        "agentos.providers.codex_cli.adapter.resolve_codex_binary",
        lambda env=None: FAKE_CODEX_SCRIPT,
    )
    binary, ver = adapter.resolve_binary({"PATH": str(tmp_path)})
    assert binary == FAKE_CODEX_SCRIPT
    assert ver == (0, 167, 0)
    assert adapter.version_supported(ver) is True
    assert adapter.binary_version(FAKE_CODEX_SCRIPT) == (0, 167, 0)


def test_codex_adapter_never_opens_auth_json_or_writes_config_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        '{"access_token":"TOP_SECRET_OAUTH"}', encoding="utf-8"
    )
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.4"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    real_open = builtins.open
    real_path_open = Path.open
    real_read_text = Path.read_text
    real_write_text = Path.write_text

    def _guard(path_obj: Any) -> None:
        lower = str(path_obj).lower()
        if "auth.json" in lower or "config.toml" in lower:
            raise AssertionError(f"Forbidden access to {path_obj}")

    monkeypatch.setattr(
        builtins,
        "open",
        lambda f, *a, **kw: (_guard(f), real_open(f, *a, **kw))[1],
    )
    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *a, **kw: (_guard(self), real_path_open(self, *a, **kw))[1],
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **kw: (_guard(self), real_read_text(self, *a, **kw))[1],
    )
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda self, *a, **kw: (_guard(self), real_write_text(self, *a, **kw))[1],
    )

    adapter = CodexCliAdapter()
    models = adapter.models()
    assert len(models) > 0
    assert adapter.context_window("gpt-6.1-sol") == 500_000

    class _Handle:
        url = "http://127.0.0.1:43123/mcp"
        token = "mcp-secret-tok"
        server_name = "orin"

    wiring = adapter.wire_tool_server(_Handle(), tmp_path)
    sys_file = tmp_path / "sys.txt"
    real_write_text(sys_file, "sys", encoding="utf-8")
    argv = adapter.build_command(
        CliTurnRequest(
            binary="codex",
            system_prompt_file=sys_file,
            model="gpt-6.1-sol",
            tool_server_wiring=wiring,
        )
    )
    logged = adapter.loggable_command(argv)
    assert "mcp-secret-tok" not in " ".join(logged)
    assert (codex_home / "config.toml").exists()


def test_native_turn_defaults_persist_session_true_for_one_shot_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-shot Codex turns must not pass --ephemeral by default so resume works on Turn 2."""
    captured_argv: list[list[str]] = []

    adapter = CodexCliAdapter()
    monkeypatch.setattr(
        adapter,
        "resolve_binary",
        lambda env=None: (str(FAKE_CODEX_SCRIPT), (0, 167, 0)),
    )

    real_build = adapter.build_command

    def _spy_build(req: CliTurnRequest, wiring: Any = None) -> list[str]:
        cmd = real_build(req, wiring)
        captured_argv.append(list(cmd))
        return cmd

    monkeypatch.setattr(adapter, "build_command", _spy_build)
    provider = ClaudeCliProvider(adapter=adapter)
    req = NativeTurnRequest(
        model="gpt-6-luna",
        system_prompt="You are Orin.",
        user_content=[{"type": "text", "text": "Hello"}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 20.0,
        max_turns=2,
        cwd=tmp_path / "workdir",
    )
    handle = start_native_turn_for_provider(provider, req)
    try:
        sink = _RecordingSink()
        handle.run_until_result(sink)
    finally:
        handle.close()

    assert len(captured_argv) == 1
    assert "--ephemeral" not in captured_argv[0]



