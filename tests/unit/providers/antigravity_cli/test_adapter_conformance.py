from __future__ import annotations

import builtins
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import pytest

from agentos.providers.antigravity_cli import (
    DEFAULT_ANTIGRAVITY_MODEL,
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    MIN_ANTIGRAVITY_VERSION,
    AntigravityCliAdapter,
    AntigravityStreamTranslator,
    build_antigravity_command,
    build_isolated_antigravity_env,
    classify_antigravity_error,
    discover_antigravity_models,
    encode_antigravity_user_turn,
    is_antigravity_version_supported,
    redact_antigravity_argv,
    resolve_antigravity_binary,
    wire_antigravity_tool_server,
)
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
)
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
from tests.unit.providers.cli_adapter_conformance import (
    AdapterFixtures,
    CliAdapterConformance,
    ErrorScenario,
    FakeToolServerHandle,
    StreamScenario,
    load_jsonl,
)

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_ANTIGRAVITY_SCRIPT = str(Path(__file__).with_name("fake_antigravity.py"))


def _make_error_result(
    text: str,
    *,
    subtype: str = "error",
    stop_reason: str | None = "error",
    api_error_status: int | None = None,
) -> CliResult:
    return CliResult(
        subtype=subtype,
        is_error=True,
        result_text=text,
        stop_reason=stop_reason,
        num_turns=1,
        duration_ms=10,
        session_id="conv-err-1",
        api_error_status=api_error_status,
        usage=CliUsage(),
        model_usage={},
    )


class TestAntigravityCliAdapterConformance(CliAdapterConformance):
    def adapter_fixtures(self) -> AdapterFixtures:
        return AdapterFixtures(
            adapter=AntigravityCliAdapter(),
            simple_text=StreamScenario(
                lines=load_jsonl(FIXTURES / "simple_text.jsonl"),
                expected_text="PONG_AGY\n",
                expected_session_id="14140a4a-4bab-4165-ab52-ee036a3ab56a",
            ),
            tool_call=StreamScenario(
                lines=load_jsonl(FIXTURES / "tool_call.jsonl"),
                expected_text="ECHO_AGY_OK:hello-from-agy\n",
                expected_tool_calls=(("echo", {"text": "hello-from-agy"}),),
                expected_session_id="2317c6f2-9d5a-42d5-87ba-4423abd1cc7a",
            ),
            error_scenarios=(
                ErrorScenario(
                    lines=load_jsonl(FIXTURES / "auth_error.jsonl"),
                    expected_kind=CliErrorKind.AUTH,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=load_jsonl(FIXTURES / "bad_model.jsonl"),
                    expected_kind=CliErrorKind.MODEL_NOT_FOUND,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=[
                        '{"event":"init","conversation_id":"c-rl","init":{"model":"gemini-3.8-flash-low","tools":[]}}',
                        '{"event":"result","result":{"conversation_id":"c-rl","status":"ERROR","error":"Error 429: RESOURCE_EXHAUSTED: Quota exceeded. Try again in 45s.","finished_reason":"error"}}',
                    ],
                    expected_kind=CliErrorKind.RATE_LIMITED,
                    expected_retryable=True,
                ),
                ErrorScenario(
                    lines=[
                        '{"event":"init","conversation_id":"c-ctx","init":{"model":"gemini-3.8-flash-low","tools":[]}}',
                        '{"event":"result","result":{"conversation_id":"c-ctx","status":"ERROR","error":"Input token limit exceeded: prompt is too long for context window","finished_reason":"error"}}',
                    ],
                    expected_kind=CliErrorKind.CONTEXT_OVERFLOW,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=[
                        '{"event":"init","conversation_id":"c-503","init":{"model":"gemini-3.8-flash-low","tools":[]}}',
                        '{"event":"result","result":{"conversation_id":"c-503","status":"ERROR","error":"503 Service Unavailable: deadline exceeded","finished_reason":"print_timeout"}}',
                    ],
                    expected_kind=CliErrorKind.TRANSIENT,
                    expected_retryable=True,
                ),
            ),
        )


@pytest.mark.parametrize(
    "status,finished_reason,error_msg,expected_kind",
    [
        ("WAITING", "", "", CliErrorKind.FATAL),
        ("CANCELED", "", "", CliErrorKind.FATAL),
        ("CANCELLED", "", "", CliErrorKind.FATAL),
        ("INTERRUPTED", "interrupted", "", CliErrorKind.FATAL),
        ("FAILED", "error", "execution failed", CliErrorKind.FATAL),
        ("ERROR", "error", "unexpected crash", CliErrorKind.FATAL),
        ("SUCCESS", "print_timeout", "", CliErrorKind.TRANSIENT),
        ("SUCCESS", "interrupted", "", CliErrorKind.FATAL),
        ("SUCCESS", "error", "", CliErrorKind.FATAL),
    ],
)
def test_status_waiting_or_canceled_is_not_success(
    status: str,
    finished_reason: str,
    error_msg: str,
    expected_kind: CliErrorKind,
) -> None:
    adapter = AntigravityCliAdapter()
    tr = adapter.stream_translator()
    init_line = json.dumps(
        {
            "event": "init",
            "conversation_id": "conv-status-test",
            "init": {"model": "gemini-3.8-flash-low", "tools": []},
        }
    )
    result_line = json.dumps(
        {
            "event": "result",
            "result": {
                "conversation_id": "conv-status-test",
                "status": status,
                "response": "partial text",
                "error": error_msg,
                "finished_reason": finished_reason,
                "num_turns": 1,
                "duration_seconds": 0.5,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "thinking_tokens": 0,
                    "cache_read_tokens": 0,
                    "total_tokens": 110,
                },
            },
        }
    )
    events = []
    events.extend(tr.feed_line(init_line))
    events.extend(tr.feed_line(result_line))
    events.extend(tr.flush_pending())

    assert tr.result is not None
    assert tr.result.is_error is True
    info = adapter.classify_error(tr.result, tr.rate_limit)
    assert info.kind == expected_kind


@pytest.mark.parametrize(
    "bad_block",
    [
        {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        {"type": "document", "data": "AAAA"},
        {"type": "audio", "data": "AAAA"},
        {"type": "video", "data": "AAAA"},
    ],
)
def test_image_content_rejected_before_spawn(
    tmp_path: Path,
    bad_block: dict[str, Any],
) -> None:
    adapter = AntigravityCliAdapter()
    content = [{"type": "text", "text": "describe this"}, bad_block]

    with pytest.raises(ValueError, match=r"(?i)(texto|imagem|mídia|stream-json)"):
        encode_antigravity_user_turn(content)

    with pytest.raises(ValueError, match=r"(?i)(texto|imagem|mídia|stream-json)"):
        adapter.encode_user_turn(content)

    spawn_calls: list[Any] = []

    def _forbidden_popen(*args: Any, **kwargs: Any) -> Any:
        spawn_calls.append((args, kwargs))
        raise AssertionError("subprocess.Popen must not be called when image content is rejected")

    provider = ClaudeCliProvider(
        binary=FAKE_ANTIGRAVITY_SCRIPT,
        adapter=adapter,
        popen_factory=_forbidden_popen,
    )
    req = NativeTurnRequest(
        model="gemini-3.8-flash-low",
        system_prompt="You are Orin.",
        user_content=content,
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 20.0,
        max_turns=2,
        cwd=tmp_path / "workdir",
    )
    with pytest.raises((ValueError, NativeLoopUnavailable)) as exc_info:
        start_native_turn_for_provider(provider, req)
    assert any(
        term in str(exc_info.value).lower()
        for term in ("texto", "imagem", "mídia", "stream-json")
    )
    assert spawn_calls == []


def test_tool_call_sequence_and_id_format() -> None:
    adapter = AntigravityCliAdapter()
    tr = adapter.stream_translator()
    lines = load_jsonl(FIXTURES / "tool_call.jsonl")

    # Feed up to step_update(tool, state=ACTIVE) -> should not emit tool events yet
    for ln in lines[:4]:
        evs = tr.feed_line(ln)
        assert not any(
            e.kind
            in (
                CliEventKind.TOOL_USE_START,
                CliEventKind.TOOL_USE_END,
                CliEventKind.TOOL_RESULT,
            )
            for e in evs
        )

    # Feed line 5: step_update(tool, state=DONE) -> emits TOOL_USE_START, TOOL_USE_END, TOOL_RESULT in sequence
    done_tool_events = list(tr.feed_line(lines[4]))
    assert [e.kind for e in done_tool_events] == [
        CliEventKind.TOOL_USE_START,
        CliEventKind.TOOL_USE_END,
        CliEventKind.TOOL_RESULT,
    ]
    expected_id = "2317c6f2-9d5a-42d5-87ba-4423abd1cc7a:2"
    for ev in done_tool_events:
        assert ev.tool_use_id == expected_id
        assert ev.tool_name == "echo"

    assert done_tool_events[0].tool_input == {"text": "hello-from-agy"}
    assert done_tool_events[1].tool_input == {"text": "hello-from-agy"}
    assert done_tool_events[2].text == "ECHO_AGY_OK:hello-from-agy"

    # Feed remaining lines and verify final result & usage (with thinking_tokens=31)
    remaining_events = []
    for ln in lines[5:]:
        remaining_events.extend(tr.feed_line(ln))
    remaining_events.extend(tr.flush_pending())

    assert tr.result is not None
    assert tr.result.is_error is False
    assert tr.result.session_id == "2317c6f2-9d5a-42d5-87ba-4423abd1cc7a"
    assert tr.result.usage == CliUsage(
        input_tokens=1519,
        output_tokens=75,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        reasoning_tokens=31,
    )


def test_usage_subtracts_cache_read_and_maps_thinking_tokens() -> None:
    tr = AntigravityStreamTranslator()
    lines = [
        json.dumps(
            {
                "event": "init",
                "conversation_id": "conv-usage-1",
                "init": {"model": "gemini-4-argon-medium", "tools": []},
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-usage-1",
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "Hello",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 40,
                        "thinking_tokens": 25,
                        "cache_read_tokens": 650,
                        "total_tokens": 1065,
                    },
                },
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-usage-1",
                    "status": "SUCCESS",
                    "response": "Hello",
                    "duration_seconds": 1.0,
                    "num_turns": 1,
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 40,
                        "thinking_tokens": 25,
                        "cache_read_tokens": 650,
                        "total_tokens": 1065,
                    },
                },
            }
        ),
    ]
    events = []
    for ln in lines:
        events.extend(tr.feed_line(ln))
    assert tr.result is not None
    assert tr.result.usage == CliUsage(
        input_tokens=350,
        output_tokens=40,
        cache_read_input_tokens=650,
        cache_creation_input_tokens=0,
        reasoning_tokens=25,
    )


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
        return ToolCallResult(text=f"ECHO_AGY_OK:{request.arguments.get('text')}")


def test_second_turn_over_persistent_stdin(tmp_path: Path) -> None:
    # Part 1: Fixture replay for Turn 1 + Turn 2 in the same translator
    adapter = AntigravityCliAdapter()
    tr = adapter.stream_translator()

    t1_events = []
    for ln in load_jsonl(FIXTURES / "simple_text.jsonl"):
        t1_events.extend(tr.feed_line(ln))
    t1_events.extend(tr.flush_pending())

    assert tr.init is not None
    assert tr.init.session_id == "14140a4a-4bab-4165-ab52-ee036a3ab56a"
    assert tr.result is not None and not tr.result.is_error
    assert tr.result.result_text == "PONG_AGY\n"
    assert tr.result.usage == CliUsage(
        input_tokens=543,
        output_tokens=5,
        cache_read_input_tokens=0,
        reasoning_tokens=None,
    )

    # Reset turn result for Turn 2 on the same persistent process
    tr.reset_result()  # type: ignore[attr-defined]
    t2_events = []
    for ln in load_jsonl(FIXTURES / "second_turn.jsonl"):
        t2_events.extend(tr.feed_line(ln))
    t2_events.extend(tr.flush_pending())

    assert tr.result is not None and not tr.result.is_error
    assert tr.result.session_id == "14140a4a-4bab-4165-ab52-ee036a3ab56a"
    assert tr.result.result_text == "PONG_AGY_T2\n"
    # Usage for Turn 2 must be the step delta (629 in, 8 out), NOT the cumulative 1172/13!
    assert tr.result.usage == CliUsage(
        input_tokens=629,
        output_tokens=8,
        cache_read_input_tokens=0,
        reasoning_tokens=None,
    )

    # Part 2: E2E subprocess test with fake_antigravity.py over persistent stdin + MCP tool call
    executor = _EchoExecutor()
    provider = ClaudeCliProvider(
        binary=FAKE_ANTIGRAVITY_SCRIPT,
        adapter=adapter,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()

    req = NativeTurnRequest(
        model="gemini-3.8-flash-low",
        system_prompt="You are Orin. CUSTOM_TOKEN:SYS_OK",
        user_content=[{"type": "text", "text": "Hello Turn 1"}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = start_native_turn_for_provider(provider, req)
    try:
        sink1 = _RecordingSink()
        out1 = handle.run_until_result(sink1)
        assert out1.init is not None
        assert out1.init.session_id == "14140a4a-4bab-4165-ab52-ee036a3ab56a"
        assert out1.final_text == "PONG_AGY:SYS_OK\n"
        assert out1.result.usage.input_tokens == 543
        assert out1.result.usage.output_tokens == 5
        assert handle.is_process_alive()

        # Send Turn 2 over persistent stdin in the SAME process
        handle.send_user([{"type": "text", "text": "Hello Turn 2"}])
        sink2 = _RecordingSink()
        out2 = handle.run_until_result(sink2)
        assert out2.final_text == "PONG_AGY_T2:SYS_OK\n"
        assert out2.result.usage.input_tokens == 629
        assert out2.result.usage.output_tokens == 8

        # Send Turn 3 triggering real MCP tool call on the SAME process
        handle.send_user([{"type": "text", "text": "Now CALL_MCP please"}])
        sink3 = _RecordingSink()
        out3 = handle.run_until_result(sink3)
        assert out3.final_text == "ECHO_AGY_OK:hello-from-agy\n"
        assert len(executor.calls) == 1
        assert executor.calls[0].name == "echo"
        assert executor.calls[0].arguments == {"text": "hello-from-agy"}
        assert sink3.tools_started == [
            ("14140a4a-4bab-4165-ab52-ee036a3ab56a:6", "echo")
        ]
        assert sink3.tools_complete == [
            (
                "14140a4a-4bab-4165-ab52-ee036a3ab56a:6",
                "echo",
                {"text": "hello-from-agy"},
            )
        ]
        # Turn 3 had two steps: (684 in, 100 cache, 62 out, 31 think) + (835 in, 200 cache, 13 out, 0 think)
        # Total Turn 3 non-cached input = (684+835) - (100+200) = 1219, cache_read = 300, out = 75, reasoning = 31
        assert out3.result.usage == CliUsage(
            input_tokens=1219,
            output_tokens=75,
            cache_read_input_tokens=300,
            cache_creation_input_tokens=0,
            reasoning_tokens=31,
        )
    finally:
        handle.close()

    # All temporary files (orin.md, mcp_config.json, orin_settings.json) must be unlinked on close()
    assert not (cwd / ".agents" / "agents" / "orin.md").exists()
    assert not (cwd / ".agents" / "mcp_config.json").exists()
    assert not (cwd / ".agents" / "orin_settings.json").exists()


def test_builtin_tool_in_step_update_triggers_security_abort(
    tmp_path: Path,
) -> None:
    adapter = AntigravityCliAdapter()
    assert "run_command" in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
    assert "write_to_file" in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
    assert "execute_browser_javascript" in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS

    executor = _EchoExecutor()
    provider = ClaudeCliProvider(
        binary=FAKE_ANTIGRAVITY_SCRIPT,
        adapter=adapter,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()

    req = NativeTurnRequest(
        model="gemini-3.8-flash-low",
        system_prompt="You are Orin.",
        user_content=[{"type": "text", "text": "Run FORBIDDEN_BUILTIN now"}],
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
        assert "run_command" in str(exc_info.value)
        assert executor.calls == []
    finally:
        handle.close()


def test_cli_registry_registers_google_antigravity_with_opt_in_gate() -> None:
    assert "google-antigravity" in CLI_PROVIDERS
    # Without risk_acknowledged_at, cli_adapter_for must return None
    assert cli_adapter_for("google-antigravity", None) is None
    assert (
        cli_adapter_for("google-antigravity", None, risk_acknowledged_at=None)
        is None
    )
    # With risk_acknowledged_at, cli_adapter_for returns AntigravityCliAdapter
    adapter = cli_adapter_for(
        "google-antigravity",
        None,
        risk_acknowledged_at="2026-11-06T22:00:00Z",
    )
    assert isinstance(adapter, AntigravityCliAdapter)
    assert adapter.name == "antigravity"


def test_wire_tool_server_and_build_command_isolation(tmp_path: Path) -> None:
    handle = FakeToolServerHandle()
    workdir = tmp_path / "agy_ws"
    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("SYSTEM_PROMPT_SECRET_CONTENT", encoding="utf-8")

    wiring = wire_antigravity_tool_server(
        handle, workdir, server_name="orin", system_prompt_file=sys_file
    )
    assert (workdir / ".git").is_dir()
    agent_md = workdir / ".agents" / "agents" / "orin.md"
    mcp_cfg = workdir / ".agents" / "mcp_config.json"
    settings_json = workdir / ".agents" / "orin_settings.json"
    assert wiring.files == (agent_md, mcp_cfg, settings_json)
    for fpath in wiring.files:
        assert fpath.is_file()

    md_text = agent_md.read_text(encoding="utf-8")
    assert "excludeDefaultComponents: true" in md_text
    assert "inheritMcp: false" in md_text
    assert handle.url in md_text
    assert f"Bearer {handle.token}" in md_text
    assert "SYSTEM_PROMPT_SECRET_CONTENT" in md_text

    settings_data = json.loads(settings_json.read_text(encoding="utf-8"))
    assert settings_data["allowNonWorkspaceAccess"] is False
    assert settings_data["enableTerminalSandbox"] is True
    assert settings_data["disableSlashCommands"] is True
    assert settings_data["permissions"]["allow"] == ["mcp(orin/*)"]
    assert "command(*)" in settings_data["permissions"]["deny"]

    req = CliTurnRequest(
        binary="agy",
        system_prompt_file=sys_file,
        model="google-antigravity/gemini-3.8-flash-low",
        effort="low",
        resume_session_id="conv-123",
        tool_server_wiring=wiring,
        capabilities=AntigravityCliAdapter.capabilities,
    )
    argv = build_antigravity_command(req, wiring)
    assert "-p" not in argv
    assert argv[:7] == [
        "agy",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
        "--model",
    ]
    assert argv[7] == "gemini-3.8-flash-low"
    assert "--effort" in argv and argv[argv.index("--effort") + 1] == "low"
    assert (
        "--conversation" in argv
        and argv[argv.index("--conversation") + 1] == "conv-123"
    )
    assert "--agent" in argv and argv[argv.index("--agent") + 1] == "orin"
    assert "--settings" in argv

    redacted = redact_antigravity_argv(argv)
    assert str(settings_json) not in " ".join(redacted)
    assert "<redacted-settings>" in redacted

    env = build_isolated_antigravity_env(
        {
            "PATH": "/usr/bin",
            "GEMINI_API_KEY": "leak-1",
            "GOOGLE_API_KEY": "leak-2",
            "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/creds.json",
            "ANTHROPIC_API_KEY": "leak-3",
            "OPENAI_API_KEY": "leak-4",
        }
    )
    for k in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        assert k not in env
    assert env["NO_COLOR"] == "1"
    assert env["TERM"] == "dumb"


def test_resolve_binary_hermetic_and_version_gate(tmp_path: Path) -> None:
    assert MIN_ANTIGRAVITY_VERSION == (1, 1, 1)
    assert is_antigravity_version_supported(None) is False
    assert is_antigravity_version_supported((1, 0, 15)) is False
    assert is_antigravity_version_supported((1, 1, 0)) is False
    assert is_antigravity_version_supported((1, 1, 1)) is True
    assert is_antigravity_version_supported((1, 5, 2)) is True
    assert is_antigravity_version_supported((2, 0, 0)) is False

    # Hermetic empty env must not leak host LOCALAPPDATA
    assert resolve_antigravity_binary({"PATH": ""}) is None

    # LocalAppData fallback on Windows
    local_app = tmp_path / "LocalAppData"
    agy_bin_dir = local_app / "agy" / "bin"
    agy_bin_dir.mkdir(parents=True)
    fake_exe = agy_bin_dir / "agy.exe"
    fake_exe.write_text("fake", encoding="utf-8")
    resolved = resolve_antigravity_binary(
        {"PATH": "", "LOCALAPPDATA": str(local_app)}
    )
    if resolved is not None:
        assert Path(resolved) == fake_exe

    models = discover_antigravity_models(
        FAKE_ANTIGRAVITY_SCRIPT, run_models_cmd=True
    )
    assert len(models) >= 13
    assert any(m.id == DEFAULT_ANTIGRAVITY_MODEL for m in models)


def test_antigravity_adapter_never_touches_user_gemini_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemini_dir = tmp_path / ".gemini" / "antigravity-cli"
    gemini_dir.mkdir(parents=True)
    (gemini_dir / "jetski_state.pbtxt").write_text(
        "oauth_token: 'SECRET'", encoding="utf-8"
    )
    (gemini_dir / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    real_open = builtins.open
    real_path_open = Path.open
    real_read_text = Path.read_text
    real_write_text = Path.write_text

    def _guard(path_obj: Any) -> None:
        norm = str(path_obj).replace("\\", "/").lower()
        if "/.gemini/" in norm:
            raise AssertionError(f"Forbidden access to ~/.gemini file: {path_obj}")

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

    adapter = AntigravityCliAdapter()
    assert len(adapter.models()) >= 13
    workdir = tmp_path / "turn_ws"
    wiring = adapter.wire_tool_server(FakeToolServerHandle(), workdir)
    sys_file = tmp_path / "sys.txt"
    real_write_text(sys_file, "sys", encoding="utf-8")
    argv = adapter.build_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=sys_file,
            model="gemini-3.8-flash-low",
            tool_server_wiring=wiring,
        )
    )
    logged = adapter.loggable_command(argv)
    assert FakeToolServerHandle.token not in " ".join(logged)


def test_adversarial_command_and_wiring_injection_guards(
    tmp_path: Path,
) -> None:
    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("sys", encoding="utf-8")

    # 1. Invalid model IDs (control chars, leading dash, shell chars)
    for bad_model in ("-p", "--dangerously-skip-permissions", "model\ninjected", "model\x00bad", "model;rm"):
        with pytest.raises(ValueError):
            build_antigravity_command(
                CliTurnRequest(
                    binary="agy",
                    system_prompt_file=sys_file,
                    model=bad_model,
                )
            )

    # 2. Invalid resume_session_id / session_id
    for bad_id in ("-flag", "conv\n1", "conv\x001", "conv space"):
        with pytest.raises(ValueError):
            build_antigravity_command(
                CliTurnRequest(
                    binary="agy",
                    system_prompt_file=sys_file,
                    model="gemini-3.8-flash-low",
                    resume_session_id=bad_id,
                )
            )
        with pytest.raises(ValueError):
            build_antigravity_command(
                CliTurnRequest(
                    binary="agy",
                    system_prompt_file=sys_file,
                    model="gemini-3.8-flash-low",
                    session_id=bad_id,
                )
            )

    # 3. Invalid effort
    for bad_effort in ("invalid_effort", "low\ninjected", "-p"):
        with pytest.raises(ValueError):
            build_antigravity_command(
                CliTurnRequest(
                    binary="agy",
                    system_prompt_file=sys_file,
                    model="gemini-3.8-flash-low",
                    effort=bad_effort,
                )
            )

    # 4. Tampered ToolServerWiring argv
    from agentos.providers.cli_adapter import ToolServerWiring

    for bad_argv in (
        ("--dangerously-skip-permissions",),
        ("--agent", "evil"),
        ("-p", "prompt"),
    ):
        with pytest.raises(ValueError):
            build_antigravity_command(
                CliTurnRequest(
                    binary="agy",
                    system_prompt_file=sys_file,
                    model="gemini-3.8-flash-low",
                ),
                ToolServerWiring(argv=bad_argv, env={}, files=()),
            )

    # 5. Invalid handle url/token/server_name in wire_antigravity_tool_server
    class _BadHandle:
        url = "http://127.0.0.1:1234/mcp\ninjected: true"
        token = "tok"

    with pytest.raises(ValueError):
        wire_antigravity_tool_server(_BadHandle(), tmp_path / "ws1")

    class _BadTokenHandle:
        url = "http://127.0.0.1:1234/mcp"
        token = "tok\x00bad"

    with pytest.raises(ValueError):
        wire_antigravity_tool_server(_BadTokenHandle(), tmp_path / "ws2")

    with pytest.raises(ValueError):
        wire_antigravity_tool_server(
            FakeToolServerHandle(), tmp_path / "ws3", server_name="bad/name"
        )


def test_adversarial_stream_and_error_classification() -> None:
    tr = AntigravityStreamTranslator()

    # 1. Non-orin MCP tool or empty prefix in step_update triggers forbidden_builtin_tool
    for bad_tool in ("mcp_evil_steal", "mcp_orin_", "", "sed_file"):
        evs = tr.feed_line(
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "c1",
                        "step_index": 1,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": bad_tool,
                    },
                }
            )
        )
        assert len(evs) == 1
        assert evs[0].kind == CliEventKind.TOOL_USE_START
        assert evs[0].tool_name in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS

    # 2. Fallback cumulative usage subtraction across turns when step_update has no usage
    tr2 = AntigravityStreamTranslator()
    tr2.feed_line(
        '{"event":"init","conversation_id":"c2","init":{"model":"gemini-3.8-flash-low","tools":[]}}'
    )
    tr2.feed_line(
        '{"event":"result","result":{"conversation_id":"c2","status":"SUCCESS","response":"T1","usage":{"input_tokens":500,"output_tokens":10,"thinking_tokens":5,"cache_read_tokens":100}}}'
    )
    assert tr2.result is not None
    assert tr2.result.usage == CliUsage(
        input_tokens=400,
        output_tokens=10,
        cache_read_input_tokens=100,
        reasoning_tokens=5,
    )
    tr2.reset_result()
    tr2.feed_line(
        '{"event":"result","result":{"conversation_id":"c2","status":"SUCCESS","response":"T2","usage":{"input_tokens":1200,"output_tokens":25,"thinking_tokens":15,"cache_read_tokens":300}}}'
    )
    assert tr2.result is not None
    assert tr2.result.usage == CliUsage(
        input_tokens=500,  # (1200 - 500) - (300 - 100) = 700 - 200 = 500
        output_tokens=15,  # 25 - 10
        cache_read_input_tokens=200,  # 300 - 100
        reasoning_tokens=10,  # 15 - 5
    )

    # 3. Error classification edge cases
    rl_info = classify_antigravity_error(
        _make_error_result("429 Quota exceeded for login session, retry in 2m")
    )
    assert rl_info.kind == CliErrorKind.RATE_LIMITED
    assert rl_info.retryable is True
    assert rl_info.retry_after_seconds == pytest.approx(120.0)

    for bad_reset in (float("nan"), float("inf"), -10.0, 0.0):
        info = classify_antigravity_error(
            _make_error_result("resource_exhausted"),
            RateLimitState(status="rejected", resets_at=bad_reset),
        )
        assert info.kind == CliErrorKind.RATE_LIMITED
        assert info.retry_after_seconds is None

    auth_info = classify_antigravity_error(
        _make_error_result("User is not logged in")
    )
    assert auth_info.kind == CliErrorKind.AUTH
    assert auth_info.status_code == 401
    assert "agy" in auth_info.user_message

