"""Adversarial Red Team suite for Phase 3 Group 3 (Tasks 10 & 11: commits d688406, 8d1bc4e, 0fc77b4).

Covers:
1. Surface 6 — CliAgentAdapter contract & ClaudeCliAdapter implementation:
   - Provider-neutral vocabulary in `src/agentos/providers/cli_adapter.py` (no references to
     `claude`, `anthropic`, `mcp__orin__`, or `opencode`).
   - Meta-testing `CliAdapterConformance` against defective mutant adapters:
     * Mutant leaking `token` in `build_command` argv
     * Mutant leaking `token` in `loggable_command`
     * Mutant mutating the caller's `base` dict in `environment(base, ...)`
     * Mutant raising an exception on invalid NDJSON in `stream_translator().feed_line("garbage")`
     * Mutant returning `retryable=True` for 404 (`MODEL_NOT_FOUND`) or invalid `exception` in `classify_error`
     * Mutant returning `<= 0` for `context_window("unknown")`
   - `ClaudeCliAdapter.capabilities` matching real runtime behavior.
   - `ClaudeCliAdapter.classify_error` across 401/403, 404, 429 / `rate_limit.status == "rejected"`
     (with `resets_at`), 500/502/503/529, `"prompt is too long"` / `"context length"`, and other fatal errors.
   - `write_tool_server_config` file creation, content (`url`, `token`), and redaction in `loggable_command`.
   - `CliStreamTranslator.result` protocol read-only property vs `NativeTurnHandle` mutation.
2. Task 11 — On-demand tools on the native path (`notifications/tools/list_changed`):
   - `TurnToolServer` responding `text/event-stream` on `POST tools/call` when `tools_changed=True`
     and `Accept` includes `text/event-stream` (SSE format, event ordering, headers).
   - `TurnToolServer` responding `application/json` when `tools_changed=False` or `Accept` omits `text/event-stream`.
   - JSON-RPC batch requests (`[call1, call2]`) and parallel calls in `ToolBatchCollector` when `tools_changed=True`.
   - `RuntimeToolExecutor.list_tools()` dynamic recalculation after `ToolSearch`, direct deferred tool call,
     `FOLLOW_UP_TOOLS` (`run_command` -> `read_process_output` + `stop_process`), and remote MCP tool activation.
   - `NativeTurnRunner` fallback when `adapter.capabilities.tool_list_changed` is `False` (`allow_deferral=False`).
3. Surface 7 — Phase 2 Regressions:
   - CLI isolation flags (`--restricted`, `--strict-mcp-config`, never `--bare`, `--safe-mode`, `--session-id`, `--resume`).
   - `apiKeySource != "none"` aborting with `CliSecurityAbort` / `ProviderTransportError`.
   - `MCP_TOOL_TIMEOUT` scrubbing and `IDLE_TIMEOUT_SECONDS` enforcement.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import inspect
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any, Mapping, Sequence
from urllib import request as urllib_request

import pytest

from agentos.agentic.agent_tools import AgentToolset, ToolDefinition, ToolOutcome
from agentos.agentic.native_turn import NativeTurnRunner, RuntimeToolExecutor
from agentos.agentic.phases import PhaseController
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers import cli_adapter
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.claude_cli.command import (
    FORBIDDEN_FLAGS,
    IDLE_TIMEOUT_SECONDS,
    build_claude_command,
    build_isolated_claude_env,
)
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    ProviderTransportError,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnHandle,
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.providers.claude_cli.provider import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ClaudeCliProvider,
)
from agentos.providers.cli_adapter import (
    CliCapabilities,
    CliErrorInfo,
    CliErrorKind,
    CliEvent,
    CliEventKind,
    CliInit,
    CliModelInfo,
    CliResult,
    CliStreamTranslator,
    CliTurnRequest,
    CliUsage,
    RateLimitState,
)
from agentos.providers.cli_tools_mcp import (
    BackgroundTurnToolServer,
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
    TurnToolServer,
)
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
)
from tests.unit.providers.claude_cli.fakes import FakeClaudePopen
from tests.unit.providers.cli_adapter_conformance import (
    AdapterFixtures,
    CliAdapterConformance,
    ErrorScenario,
    FakeToolServerHandle,
    StreamScenario,
)


# ============================================================================
# Helpers & Base Conforming Adapter for Mutation Testing
# ============================================================================


class _ValidFakeTranslator:
    """Minimal conforming translator used as the baseline for mutant tests."""

    def __init__(self) -> None:
        self._init: CliInit | None = None
        self._result: CliResult | None = None
        self._rate_limit: RateLimitState | None = None
        self._stop_reason: str | None = None

    @property
    def init(self) -> CliInit | None:
        return self._init

    @property
    def result(self) -> CliResult | None:
        return self._result

    @property
    def rate_limit(self) -> RateLimitState | None:
        return self._rate_limit

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    def latest_usage(self) -> CliUsage | None:
        return self._result.usage if self._result is not None else None

    def flush_pending(self) -> Sequence[CliEvent]:
        return ()

    def feed_line(self, line: str) -> Sequence[CliEvent]:
        text = line.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        kind = data.get("type")
        if kind == "init":
            self._init = CliInit(
                session_id=str(data.get("session_id") or "s-1"),
                model="fake-m",
                api_key_source=str(data.get("apiKeySource", "none")),
                mcp_servers={"orin": "connected"},
                tools=(),
            )
            return [CliEvent(kind=CliEventKind.INIT, init=self._init, raw=data)]
        if kind == "text":
            return [CliEvent(kind=CliEventKind.TEXT_DELTA, text=str(data.get("delta") or ""), raw=data)]
        if kind == "tool":
            tname = str(data.get("name") or "mcp__orin__echo")
            tinput = dict(data.get("input") or {})
            return [
                CliEvent(kind=CliEventKind.TOOL_USE_START, tool_use_id="u1", tool_name=tname, raw=data),
                CliEvent(kind=CliEventKind.TOOL_USE_END, tool_use_id="u1", tool_name=tname, tool_input=tinput, raw=data),
            ]
        if kind == "done":
            is_err = bool(data.get("is_error", False))
            status = data.get("api_error_status")
            self._result = CliResult(
                subtype="error" if is_err else "success",
                is_error=is_err,
                result_text=str(data.get("text") or ""),
                stop_reason="end_turn",
                num_turns=1,
                duration_ms=5,
                session_id="s-1",
                api_error_status=int(status) if isinstance(status, int) else None,
                usage=CliUsage(input_tokens=10, output_tokens=5),
            )
            return [CliEvent(kind=CliEventKind.RESULT, result=self._result, raw=data)]
        return []


class _ValidFakeAdapter:
    """Baseline adapter that satisfies every check in CliAdapterConformance."""

    name = "fake_cli"
    capabilities = CliCapabilities(
        native_tool_loop=True,
        tool_list_changed=True,
        images_in_tool_results=True,
        persistent_stdin=True,
    )

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        return "/usr/bin/fake-cli", (2, 1, 310)

    def version_supported(self, version: tuple[int, int, int] | None) -> bool:
        if version is None:
            return False
        return version >= (2, 1, 309)

    def write_tool_server_config(self, handle: Any, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        cfg = directory / "mcp.json"
        cfg.write_text(handle.mcp_config_json(), encoding="utf-8")
        return cfg

    def build_command(self, request: CliTurnRequest) -> list[str]:
        cmd = [request.binary, "--model", request.model or "fake-m"]
        cmd.extend(["--system-prompt-file", str(request.system_prompt_file)])
        if request.tool_server_config_file is not None:
            cmd.extend(["--mcp-config", str(request.tool_server_config_file)])
        return cmd

    def loggable_command(self, argv: Sequence[str]) -> list[str]:
        out: list[str] = []
        skip_next = False
        for token in argv:
            if skip_next:
                out.append("<redacted-path>")
                skip_next = False
                continue
            if token in {"--system-prompt-file", "--mcp-config"}:
                out.append(token)
                skip_next = True
            else:
                out.append(token)
        return out

    def environment(
        self,
        base: Mapping[str, str] | None,
        *,
        max_output_tokens: int | None,
        tool_timeout_ms: int | None,
    ) -> dict[str, str]:
        source = dict(base) if base is not None else {}
        stripped = {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        }
        return {k: v for k, v in source.items() if k.upper() not in stripped}

    def stream_translator(self) -> CliStreamTranslator:
        return _ValidFakeTranslator()

    def classify_error(
        self,
        result: CliResult,
        rate_limit: RateLimitState | None,
    ) -> CliErrorInfo:
        status = result.api_error_status
        text_lower = (result.result_text or "").lower()
        if rate_limit is not None and rate_limit.status == "rejected":
            return CliErrorInfo(
                kind=CliErrorKind.RATE_LIMITED,
                status_code=status,
                user_message="rate limited",
                retryable=False,
            )
        if status == 429:
            return CliErrorInfo(
                kind=CliErrorKind.RATE_LIMITED,
                status_code=429,
                user_message="429",
                retryable=True,
            )
        if status in (401, 403):
            return CliErrorInfo(
                kind=CliErrorKind.AUTH,
                status_code=status,
                user_message="auth",
                retryable=False,
            )
        if status == 404:
            return CliErrorInfo(
                kind=CliErrorKind.MODEL_NOT_FOUND,
                status_code=404,
                user_message="404",
                retryable=False,
            )
        if "prompt is too long" in text_lower or "context length" in text_lower:
            return CliErrorInfo(
                kind=CliErrorKind.CONTEXT_OVERFLOW,
                status_code=status,
                user_message="overflow",
                retryable=False,
            )
        if status in (500, 502, 503, 529):
            return CliErrorInfo(
                kind=CliErrorKind.TRANSIENT,
                status_code=status,
                user_message="transient",
                retryable=True,
            )
        return CliErrorInfo(
            kind=CliErrorKind.FATAL,
            status_code=status,
            user_message="fatal",
            retryable=False,
        )

    def models(self) -> Sequence[CliModelInfo]:
        return (
            CliModelInfo(
                id="fake-m",
                display_name="Fake Model",
                context_window=128_000,
            ),
        )

    def context_window(self, model_id: str | None) -> int:
        return 128_000


def _make_conformance_harness(adapter_instance: Any) -> CliAdapterConformance:
    class _Harness(CliAdapterConformance):
        def adapter_fixtures(self) -> AdapterFixtures:
            return AdapterFixtures(
                adapter=adapter_instance,
                simple_text=StreamScenario(
                    lines=(
                        json.dumps({"type": "init", "session_id": "s-1", "apiKeySource": "none"}),
                        json.dumps({"type": "text", "delta": "Hello"}),
                        json.dumps({"type": "done", "text": "Hello", "is_error": False}),
                    ),
                    expected_text="Hello",
                    expected_session_id="s-1",
                ),
                tool_call=StreamScenario(
                    lines=(
                        json.dumps({"type": "init", "session_id": "s-1", "apiKeySource": "none"}),
                        json.dumps({"type": "tool", "name": "mcp__orin__echo", "input": {"text": "A"}}),
                        json.dumps({"type": "done", "text": "OK", "is_error": False}),
                    ),
                    expected_text="",
                    expected_tool_calls=(("mcp__orin__echo", {"text": "A"}),),
                    expected_session_id="s-1",
                ),
                error_scenarios=(
                    ErrorScenario(
                        lines=(
                            json.dumps(
                                {
                                    "type": "done",
                                    "text": "API Error: 404 model not found",
                                    "is_error": True,
                                    "api_error_status": 404,
                                }
                            ),
                        ),
                        expected_kind=CliErrorKind.MODEL_NOT_FOUND,
                        expected_retryable=False,
                    ),
                ),
            )

    return _Harness()


# ============================================================================
# SURFACE 6: CliAgentAdapter Contract & Conformance Suite Mutation Tests
# ============================================================================


def test_s6_cli_adapter_module_has_no_provider_specific_vocabulary() -> None:
    """Surface 6.1 (BUG REPRO): `cli_adapter.py` must be 100% provider-neutral (no references to
    `claude`, `anthropic`, `mcp__orin__`, or `opencode` anywhere in the module).

    Bug in `src/agentos/providers/cli_adapter.py:18`:
    `StreamUsage` docstring contains `"Extended usage snapshot preserving Anthropic prompt cache..."`.
    """
    source = inspect.getsource(cli_adapter)
    lowered = source.lower()
    forbidden_terms = ("claude", "anthropic", "mcp__orin__", "opencode")
    leaked = [term for term in forbidden_terms if term in lowered]
    assert not leaked, (
        f"Provider-specific terms {leaked} leaked into neutral contract module cli_adapter.py"
    )


def test_s6_cli_adapter_ast_identifiers_are_provider_neutral() -> None:
    """Verify no class, function, argument, or attribute identifier in `cli_adapter.py`
    references `claude`, `anthropic`, `mcp__orin__`, or `opencode`.
    """
    source = inspect.getsource(cli_adapter)
    tree = ast.parse(source)
    forbidden = ("claude", "anthropic", "mcp__orin__", "opencode")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for term in forbidden:
                assert term not in node.name.lower(), f"Identifier {node.name!r} contains {term!r}"
        elif isinstance(node, ast.Name):
            for term in forbidden:
                assert term not in node.id.lower(), f"Name {node.id!r} contains {term!r}"


def test_s6_conformance_catches_mutant_leaking_token_in_build_command(tmp_path: Path) -> None:
    """Surface 6.2a: Mutant adapter that leaks the secret MCP token into `build_command` argv
    MUST fail `CliAdapterConformance.test_command_redacts_secrets_and_omits_prompt_text`.
    """
    class _LeakingBuildCommandAdapter(_ValidFakeAdapter):
        def build_command(self, request: CliTurnRequest) -> list[str]:
            cmd = super().build_command(request)
            cmd.extend(["--token", FakeToolServerHandle.token])
            return cmd

    harness = _make_conformance_harness(_LeakingBuildCommandAdapter())
    with pytest.raises(AssertionError):
        harness.test_command_redacts_secrets_and_omits_prompt_text(tmp_path)


def test_s6_conformance_catches_mutant_leaking_token_in_loggable_command(tmp_path: Path) -> None:
    """Surface 6.2b (BUG REPRO): Mutant adapter whose `build_command` does not contain the token,
    but whose `loggable_command` leaks `FakeToolServerHandle.token` (while redacting the file path)
    MUST fail `CliAdapterConformance.test_command_redacts_secrets_and_omits_prompt_text`!

    Bug in `tests/unit/providers/cli_adapter_conformance.py:112-114`:
    `test_command_redacts_secrets_and_omits_prompt_text` checks `str(cfg) not in " ".join(logged)`,
    but NEVER checks `FakeToolServerHandle.token not in " ".join(logged)`!
    """
    class _LeakingLoggableCommandAdapter(_ValidFakeAdapter):
        def loggable_command(self, argv: Sequence[str]) -> list[str]:
            logged = super().loggable_command(argv)
            logged.append(f"auth={FakeToolServerHandle.token}")
            return logged

    harness = _make_conformance_harness(_LeakingLoggableCommandAdapter())
    with pytest.raises(AssertionError):
        harness.test_command_redacts_secrets_and_omits_prompt_text(tmp_path)


def test_s6_conformance_catches_mutant_mutating_environment_base_dict() -> None:
    """Surface 6.2c (BUG REPRO): Mutant adapter whose `environment(base, ...)` mutates the caller's
    `base` dict in-place MUST fail `CliAdapterConformance.test_environment_strips_cloud_api_keys`!

    Bug in `tests/unit/providers/cli_adapter_conformance.py:116-129`:
    `test_environment_strips_cloud_api_keys` passes `dirty` to `fx.adapter.environment(dirty, ...)`
    and checks `key not in clean`, but NEVER checks `clean is not dirty` or that `dirty` still
    contains its original keys!
    """
    class _MutatingEnvAdapter(_ValidFakeAdapter):
        def environment(
            self,
            base: Mapping[str, str] | None,
            *,
            max_output_tokens: int | None,
            tool_timeout_ms: int | None,
        ) -> dict[str, str]:
            if not isinstance(base, dict):
                return {}
            for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_API_KEY"):
                base.pop(key, None)
            return base

    harness = _make_conformance_harness(_MutatingEnvAdapter())
    with pytest.raises(AssertionError):
        harness.test_environment_strips_cloud_api_keys()


def test_s6_conformance_catches_mutant_raising_on_garbage_stream_line() -> None:
    """Surface 6.2d (BUG REPRO): Mutant adapter whose `stream_translator().feed_line("garbage")`
    raises an exception on invalid JSON MUST fail `CliAdapterConformance.test_stream_ignores_malformed_lines`
    with `AssertionError` (or `CliAdapterConformance` must catch/assert no exception is raised).
    """
    class _CrashingTranslator(_ValidFakeTranslator):
        def feed_line(self, line: str) -> Sequence[CliEvent]:
            if line:
                json.loads(line)  # Unhandled JSONDecodeError on malformed line
            return super().feed_line(line)

    class _CrashingStreamAdapter(_ValidFakeAdapter):
        def stream_translator(self) -> CliStreamTranslator:
            return _CrashingTranslator()

    harness = _make_conformance_harness(_CrashingStreamAdapter())
    with pytest.raises(AssertionError):
        harness.test_stream_ignores_malformed_lines()


def test_s6_conformance_catches_mutant_404_retryable_true() -> None:
    """Surface 6.2e (part 1): Mutant adapter whose `classify_error` returns `retryable=True`
    for 404 (`MODEL_NOT_FOUND`) MUST fail `CliAdapterConformance.test_error_classification_matches_scenarios`.
    """
    class _Retryable404Adapter(_ValidFakeAdapter):
        def classify_error(
            self,
            result: CliResult,
            rate_limit: RateLimitState | None,
        ) -> CliErrorInfo:
            info = super().classify_error(result, rate_limit)
            if info.kind == CliErrorKind.MODEL_NOT_FOUND:
                return replace(info, retryable=True)
            return info

    harness = _make_conformance_harness(_Retryable404Adapter())
    with pytest.raises(AssertionError):
        harness.test_error_classification_matches_scenarios()


def test_s6_conformance_catches_mutant_classify_error_non_exception() -> None:
    """Surface 6.2e (part 2, BUG REPRO): Per Task 10 spec (`docs/superpowers/plans/2026-11-05-fase-3-paridade-e-base-multi-cli.md:649,732`),
    `CliErrorInfo` must carry `exception: Exception` and `CliAdapterConformance.test_error_classification_matches_scenarios`
    must assert `isinstance(info.exception, Exception)`. A mutant returning `exception="not-an-exception"` must fail with `AssertionError`.
    """
    class _NonExceptionErrorAdapter(_ValidFakeAdapter):
        def classify_error(
            self,
            result: CliResult,
            rate_limit: RateLimitState | None,
        ) -> Any:
            info = super().classify_error(result, rate_limit)
            return type(
                "BrokenErrorInfo",
                (),
                {
                    "kind": info.kind,
                    "status_code": info.status_code,
                    "user_message": info.user_message,
                    "retryable": info.retryable,
                    "retry_after_seconds": info.retry_after_seconds,
                    "exception": "not-an-exception-instance",
                },
            )()

    harness = _make_conformance_harness(_NonExceptionErrorAdapter())
    with pytest.raises(AssertionError):
        harness.test_error_classification_matches_scenarios()


def test_s6_conformance_catches_mutant_non_positive_context_window() -> None:
    """Surface 6.2f: Mutant adapter whose `context_window("unknown")` returns `<= 0`
    MUST fail `CliAdapterConformance.test_context_window_known_and_unknown_models`.
    """
    class _ZeroContextWindowAdapter(_ValidFakeAdapter):
        def context_window(self, model_id: str | None) -> int:
            if model_id and "unknown" in model_id:
                return 0
            return 128_000

    harness = _make_conformance_harness(_ZeroContextWindowAdapter())
    with pytest.raises(AssertionError):
        harness.test_context_window_known_and_unknown_models()


# ============================================================================
# SURFACE 6: ClaudeCliAdapter Capabilities, Error Classification & Config File
# ============================================================================


def test_s6_claude_cli_adapter_capabilities_match_real_behavior() -> None:
    """Surface 6.3: `ClaudeCliAdapter.capabilities` must accurately declare its real capabilities."""
    adapter = ClaudeCliAdapter()
    caps = adapter.capabilities
    assert caps.native_tool_loop is True
    assert caps.tool_list_changed is True
    assert caps.images_in_tool_results is True
    assert caps.persistent_stdin is True


@pytest.mark.parametrize(
    ("status_code", "expected_kind", "expected_retryable"),
    [
        (401, CliErrorKind.AUTH, False),
        (403, CliErrorKind.AUTH, False),
        (404, CliErrorKind.MODEL_NOT_FOUND, False),
        (429, CliErrorKind.RATE_LIMITED, False),
        (500, CliErrorKind.TRANSIENT, True),
        (502, CliErrorKind.TRANSIENT, True),
        (503, CliErrorKind.TRANSIENT, True),
        (529, CliErrorKind.TRANSIENT, True),
    ],
)
def test_s6_claude_cli_adapter_classify_error_http_statuses(
    status_code: int,
    expected_kind: CliErrorKind,
    expected_retryable: bool,
) -> None:
    """Surface 6.4a: `ClaudeCliAdapter.classify_error` maps HTTP status codes accurately."""
    adapter = ClaudeCliAdapter()
    result = CliResult(
        subtype="error_during_execution",
        is_error=True,
        result_text=f"API Error: {status_code} failure",
        stop_reason="error",
        num_turns=1,
        duration_ms=10,
        session_id="s-err",
        api_error_status=status_code,
        usage=CliUsage(),
    )
    info = adapter.classify_error(result, None)
    assert info.kind == expected_kind
    assert info.retryable is expected_retryable
    assert info.status_code == status_code
    assert info.user_message


def test_s6_claude_cli_adapter_classify_error_rate_limit_rejected_and_resets_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface 6.4b (BUG REPRO): `rate_limit.status == "rejected"` and 429 with `resets_at` classify
    as `CliErrorKind.RATE_LIMITED` and compute `retry_after_seconds` from `resets_at`.

    Bug in `src/agentos/providers/claude_cli/errors.py:78-81`:
    `classify_result_error` only computes `retry_after_seconds` from `rate_limit.resets_at` when
    `subscription_rejected` (`rate_limit.status == "rejected"`) is True. When `api_error_status == 429`
    and `rate_limit` has `status="allowed_warning"` with `resets_at`, `retry_after_seconds` is `None`!
    """
    monkeypatch.setattr("agentos.providers.claude_cli.errors.time.time", lambda: 1_700_000_000.0)
    adapter = ClaudeCliAdapter()

    # Case 1: subscription rate_limit_event with status="rejected" and resets_at=1_700_000_045.0
    res_sub = CliResult(
        subtype="error_during_execution",
        is_error=True,
        result_text="You've hit your limit",
        stop_reason="error",
        num_turns=1,
        duration_ms=10,
        session_id="s-rl",
        api_error_status=None,
        usage=CliUsage(),
    )
    rl_rejected = RateLimitState(
        status="rejected",
        rate_limit_type="five_hour",
        resets_at=1_700_000_045.0,
    )
    info_sub = adapter.classify_error(res_sub, rl_rejected)
    assert info_sub.kind == CliErrorKind.RATE_LIMITED
    assert info_sub.retryable is False
    assert info_sub.retry_after_seconds == pytest.approx(45.0)

    # Case 2: HTTP 429 accompanied by a rate_limit snapshot containing resets_at=1_700_000_030.0
    res_429 = CliResult(
        subtype="error_during_execution",
        is_error=True,
        result_text="API Error: 429 Too Many Requests",
        stop_reason="error",
        num_turns=1,
        duration_ms=10,
        session_id="s-429",
        api_error_status=429,
        usage=CliUsage(),
    )
    rl_warning = RateLimitState(
        status="allowed_warning",
        rate_limit_type="five_hour",
        resets_at=1_700_000_030.0,
    )
    info_429 = adapter.classify_error(res_429, rl_warning)
    assert info_429.kind == CliErrorKind.RATE_LIMITED
    assert info_429.retry_after_seconds == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("result_text", "api_error_status"),
    [
        ("Prompt is too long: 210000 tokens > 200000 maximum", 400),
        ("Prompt is too long", None),
        ("Maximum context length exceeded for this model", 400),
        ("Maximum context length exceeded for this model", None),
    ],
)
def test_s6_claude_cli_adapter_classify_error_context_overflow(
    result_text: str,
    api_error_status: int | None,
) -> None:
    """Surface 6.4c (BUG REPRO): `"prompt is too long"` and `"context length"` must ALWAYS classify
    as `CliErrorKind.CONTEXT_OVERFLOW` with `retryable=False`, whether `api_error_status` is 400 or `None`!

    Bug in `src/agentos/providers/claude_cli/errors.py:124,152-156`:
    1. When `api_error_status is None`, `classify_result_error` sets `effective_status = 500`, and
       `classify_result_error_info` checks `500 <= status <= 599` BEFORE checking `"prompt is too long"`,
       returning `CliErrorKind.TRANSIENT` (`retryable=True`) instead of `CONTEXT_OVERFLOW` (`retryable=False`)!
    2. `errors.py:156` checks `"context_length_exceeded"` (with underscore) instead of `"context length"`
       (with space), missing `"Maximum context length exceeded"`.
    """
    adapter = ClaudeCliAdapter()
    result = CliResult(
        subtype="error_during_execution",
        is_error=True,
        result_text=result_text,
        stop_reason="error",
        num_turns=1,
        duration_ms=10,
        session_id="s-ctx",
        api_error_status=api_error_status,
        usage=CliUsage(),
    )
    info = adapter.classify_error(result, None)
    assert info.kind == CliErrorKind.CONTEXT_OVERFLOW
    assert info.retryable is False


@pytest.mark.parametrize("api_error_status", [None, 400, 422])
def test_s6_claude_cli_adapter_classify_error_other_errors_are_fatal(
    api_error_status: int | None,
) -> None:
    """Surface 6.4d (BUG REPRO): Unrecognized CLI errors (including when `api_error_status is None`)
    must classify as `CliErrorKind.FATAL` with `retryable=False`.

    Bug in `src/agentos/providers/claude_cli/errors.py:124,152`:
    Because `classify_result_error` defaults `effective_status = 500` when `status is None`,
    `classify_result_error_info` hits `500 <= status <= 599` and classifies EVERY statusless CLI error
    as `CliErrorKind.TRANSIENT` (`retryable=True`) — making `else: kind = CliErrorKind.FATAL` unreachable
    whenever `api_error_status is None`!
    """
    adapter = ClaudeCliAdapter()
    result = CliResult(
        subtype="error_during_execution",
        is_error=True,
        result_text="Invalid tool schema or fatal CLI configuration error",
        stop_reason="error",
        num_turns=1,
        duration_ms=10,
        session_id="s-fatal",
        api_error_status=api_error_status,
        usage=CliUsage(),
    )
    info = adapter.classify_error(result, None)
    assert info.kind == CliErrorKind.FATAL
    assert info.retryable is False


def test_s6_write_tool_server_config_and_loggable_command_redaction(tmp_path: Path) -> None:
    """Surface 6.5: `write_tool_server_config` creates the config file inside the target directory,
    writes the URL and Bearer token, and never leaks the token or path in `loggable_command`.
    """
    adapter = ClaudeCliAdapter()

    class _CustomHandle:
        url = "http://127.0.0.1:54321/mcp"
        token = "super-secret-orin-mcp-token-999"
        server_name = "orin"

        def mcp_config_json(self) -> str:
            return json.dumps(
                {
                    "mcpServers": {
                        self.server_name: {
                            "type": "http",
                            "url": self.url,
                            "headers": {"Authorization": f"Bearer {self.token}"},
                        }
                    }
                }
            )

    handle = _CustomHandle()
    target_dir = tmp_path / "isolated_mcp_dir"
    target_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = adapter.write_tool_server_config(handle, target_dir)  # type: ignore[arg-type]

    assert cfg_path.exists()
    assert cfg_path.parent.resolve() == target_dir.resolve()
    raw_cfg = cfg_path.read_text(encoding="utf-8")
    parsed_cfg = json.loads(raw_cfg)
    assert parsed_cfg["mcpServers"]["orin"]["url"] == "http://127.0.0.1:54321/mcp"
    assert parsed_cfg["mcpServers"]["orin"]["headers"]["Authorization"] == f"Bearer {handle.token}"

    prompt_file = target_dir / "system_prompt.txt"
    prompt_file.write_text("CONFIDENTIAL_SYSTEM_INSTRUCTIONS", encoding="utf-8")

    req = CliTurnRequest(
        binary="C:/fake/claude.exe",
        system_prompt_file=prompt_file,
        model="claude-opus-4-7",
        tool_server_config_file=cfg_path,
    )
    argv = adapter.build_command(req)
    logged = adapter.loggable_command(argv)

    assert handle.token not in " ".join(argv)
    assert handle.token not in " ".join(logged)
    assert "CONFIDENTIAL_SYSTEM_INSTRUCTIONS" not in " ".join(argv)
    assert "CONFIDENTIAL_SYSTEM_INSTRUCTIONS" not in " ".join(logged)
    assert str(cfg_path) not in " ".join(logged)
    assert str(prompt_file) not in " ".join(logged)


def test_s6_native_turn_handle_works_with_readonly_result_property_on_stream_translator() -> None:
    """Surface 6.6 (BUG REPRO): `CliStreamTranslator` defines `result` as a read-only `@property`
    (`src/agentos/providers/cli_adapter.py:227-228`), but `NativeTurnHandle.run_until_result`
    (`src/agentos/providers/claude_cli/native_loop.py:400`) executes `self._parser.result = None`
    which raises `AttributeError: property 'result' of '...' object has no setter` on any translator
    implementing the protocol strictly with `@property`!
    """
    class _StrictProtocolTranslator:
        def __init__(self) -> None:
            self._init = CliInit(
                session_id="s-ro",
                model="claude-opus-4-7",
                api_key_source="none",
                mcp_servers={"orin": "connected"},
                tools=(),
            )
            self._result: CliResult | None = None

        @property
        def init(self) -> CliInit | None:
            return self._init

        @property
        def result(self) -> CliResult | None:
            return self._result

        @property
        def rate_limit(self) -> RateLimitState | None:
            return None

        @property
        def stop_reason(self) -> str | None:
            return "end_turn"

        def latest_usage(self) -> CliUsage | None:
            return CliUsage(input_tokens=1, output_tokens=1)

        def flush_pending(self) -> Sequence[CliEvent]:
            return ()

        def feed_line(self, line: str) -> Sequence[CliEvent]:
            self._result = CliResult(
                subtype="success",
                is_error=False,
                result_text="ok",
                stop_reason="end_turn",
                num_turns=1,
                duration_ms=5,
                session_id="s-ro",
                api_error_status=None,
                usage=CliUsage(input_tokens=1, output_tokens=1),
            )
            return [
                CliEvent(kind=CliEventKind.INIT, init=self._init),
                CliEvent(kind=CliEventKind.RESULT, result=self._result),
            ]

    class _StrictAdapter(ClaudeCliAdapter):
        def stream_translator(self) -> CliStreamTranslator:
            return _StrictProtocolTranslator()

    class _DummySlots:
        def acquire(self, **_: Any) -> bool:
            return True

        def release(self) -> None:
            pass

    class _DummyBgServer:
        class handle:
            url = "http://127.0.0.1:0/mcp"
            in_flight = 0

            @staticmethod
            def refuse_calls(reason: str) -> None:
                pass

            @staticmethod
            def allow_calls() -> None:
                pass

        def close(self, timeout: float = 1.0) -> None:
            pass

    q: queue.Queue[str | None] = queue.Queue()
    q.put('{"type":"result"}')
    q.put(None)
    dummy_thread = threading.Thread(target=lambda: None)
    dummy_thread.start()
    proc = FakeClaudePopen(["claude"], ndjson_lines=[])

    handle = NativeTurnHandle(
        proc=proc,
        cmd=["claude"],
        requested_model="claude-opus-4-7",
        server_name="orin",
        bg_server=_DummyBgServer(),  # type: ignore[arg-type]
        temp_files=(),
        slots=_DummySlots(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 10.0,
        clock=time.monotonic,
        kill_tree_fn=lambda p: None,
        log_run_fn=lambda **kw: None,
        stdout_queue=q,
        stderr_chunks=[],
        stdout_thread=dummy_thread,
        stderr_thread=dummy_thread,
        t0=time.monotonic(),
        adapter=_StrictAdapter(),
    )
    class _NoopSink:
        def on_init(self, init: CliInit) -> None:
            pass

        def on_text(self, text: str) -> None:
            pass

        def on_thinking(self, text: str) -> None:
            pass

        def on_tool_use_started(self, tool_use_id: str, tool_name: str, message_id: str | None = None) -> None:
            pass

        def on_tool_input_delta(self, tool_use_id: str, tool_name: str, new_bytes: int) -> None:
            pass

        def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None:
            pass

        def on_rate_limit(self, state: RateLimitState) -> None:
            pass

        def on_compact_boundary(self) -> None:
            pass

    try:
        outcome = handle.run_until_result(_NoopSink())  # type: ignore[arg-type]
        assert outcome.result.result_text == "ok"
    finally:
        handle.close()


# ============================================================================
# TASK 11: On-Demand Tools on Native Path (`notifications/tools/list_changed`)
# ============================================================================


class _StubListChangedExecutor:
    def __init__(self, *, tools_changed: bool = False) -> None:
        self.tools_changed = tools_changed
        self.calls: list[ToolCallRequest] = []

    def list_tools(self) -> list[McpToolSpec]:
        return [
            McpToolSpec(
                name="ToolSearch",
                description="Search deferred tools",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            )
        ]

    def is_known_tool(self, name: str) -> bool:
        return name in {"ToolSearch", "Read"}

    def call_tool(self, req: ToolCallRequest) -> ToolCallResult:
        self.calls.append(req)
        return ToolCallResult(
            text=f"ran:{req.name}",
            is_error=False,
            tools_changed=self.tools_changed and req.name == "ToolSearch",
        )


def _post_mcp(
    url: str,
    token: str,
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    accept: str = "application/json, text/event-stream",
) -> tuple[int, dict[str, str], str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": accept,
        },
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=5) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, headers, resp.read().decode("utf-8")


def _parse_sse_frames(raw_body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse SSE frames (`event: ...\ndata: ...\n\n`) and validate framing."""
    assert raw_body.endswith("\n\n"), f"SSE stream must end with double newline, got: {raw_body!r}"
    chunks = [chunk for chunk in raw_body.split("\n\n") if chunk.strip()]
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        assert len(lines) == 2, f"Expected 2-line SSE frame (event + data), got: {lines!r}"
        assert lines[0].startswith("event: "), f"Missing 'event: ' prefix in {lines[0]!r}"
        assert lines[1].startswith("data: "), f"Missing 'data: ' prefix in {lines[1]!r}"
        event_name = lines[0][len("event: ") :]
        data_obj = json.loads(lines[1][len("data: ") :])
        frames.append((event_name, data_obj))
    return frames


def test_t11_turn_tool_server_sse_format_order_and_headers_when_tools_changed() -> None:
    """Task 11.1: When `tools_changed=True` and `Accept` contains `text/event-stream`,
    `TurnToolServer` responds with `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
    and two valid SSE frames in exact order:
      1. `notifications/tools/list_changed`
      2. JSON-RPC response with matching `id`.
    """
    executor = _StubListChangedExecutor(tools_changed=True)
    bg = BackgroundTurnToolServer.start(TurnToolServer(executor))
    handle = bg.handle
    try:
        status, headers, raw = _post_mcp(
            handle.url,
            handle.token,
            {
                "jsonrpc": "2.0",
                "id": 77,
                "method": "tools/call",
                "params": {"name": "ToolSearch", "arguments": {"query": "select:WebFetch"}},
            },
            accept="application/json, text/event-stream",
        )
    finally:
        bg.close()

    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert headers.get("cache-control") == "no-cache"
    assert "x-orin-tools-changed" not in headers

    frames = _parse_sse_frames(raw)
    assert len(frames) == 2
    assert frames[0][0] == "message"
    assert frames[0][1] == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    assert frames[1][0] == "message"
    assert frames[1][1]["jsonrpc"] == "2.0"
    assert frames[1][1]["id"] == 77
    assert frames[1][1]["result"]["content"][0]["text"] == "ran:ToolSearch"


def test_t11_turn_tool_server_returns_json_when_tools_not_changed_or_accept_is_json() -> None:
    """Task 11.2: When `tools_changed=False` OR `Accept` does not include `text/event-stream`,
    `TurnToolServer` returns a normal `application/json` response without leaking internal headers.
    """
    # Case A: tools_changed=False with Accept: application/json, text/event-stream
    exec_unchanged = _StubListChangedExecutor(tools_changed=False)
    bg_a = BackgroundTurnToolServer.start(TurnToolServer(exec_unchanged))
    handle_a = bg_a.handle
    try:
        status_a, headers_a, raw_a = _post_mcp(
            handle_a.url,
            handle_a.token,
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "ToolSearch", "arguments": {"query": "select:WebFetch"}},
            },
            accept="application/json, text/event-stream",
        )
    finally:
        bg_a.close()
    assert status_a == 200
    assert headers_a["content-type"].startswith("application/json")
    assert "x-orin-tools-changed" not in headers_a
    parsed_a = json.loads(raw_a)
    assert parsed_a["id"] == 11

    # Case B: tools_changed=True but Accept: application/json only
    exec_changed = _StubListChangedExecutor(tools_changed=True)
    bg_b = BackgroundTurnToolServer.start(TurnToolServer(exec_changed))
    handle_b = bg_b.handle
    try:
        status_b, headers_b, raw_b = _post_mcp(
            handle_b.url,
            handle_b.token,
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "ToolSearch", "arguments": {"query": "select:WebFetch"}},
            },
            accept="application/json",
        )
    finally:
        bg_b.close()
    assert status_b == 200
    assert headers_b["content-type"].startswith("application/json")
    assert "x-orin-tools-changed" not in headers_b
    parsed_b = json.loads(raw_b)
    assert parsed_b["id"] == 12


def test_t11_jsonrpc_batch_request_preserves_tools_changed_notification() -> None:
    """Task 11.3a (BUG REPRO): In a JSON-RPC batch request `[call1, call2]` where `call1` triggers
    `tools_changed=True` and `call2` does not (with `Accept: application/json, text/event-stream`),
    `TurnToolServer` must NOT silently discard `X-Orin-Tools-Changed`!

    Bug in `src/agentos/providers/cli_tools_mcp/server.py:506`:
    When `isinstance(payload, list)`, `_mcp_endpoint` loops over items and calls
    `extra_headers.pop("X-Orin-Tools-Changed", None)` without recording that tools changed,
    and returns a plain `JSONResponse(responses)` without `notifications/tools/list_changed`!
    """
    executor = _StubListChangedExecutor(tools_changed=True)
    bg = BackgroundTurnToolServer.start(TurnToolServer(executor))
    handle = bg.handle
    try:
        status, headers, raw = _post_mcp(
            handle.url,
            handle.token,
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "ToolSearch", "arguments": {"query": "select:WebFetch"}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "Read", "arguments": {}},
                },
            ],
            accept="application/json, text/event-stream",
        )
    finally:
        bg.close()

    assert status == 200
    assert "notifications/tools/list_changed" in raw, (
        "JSON-RPC batch request containing a tool discovery call dropped notifications/tools/list_changed!"
    )


def _make_native_executor(
    tmp_path: Path,
    *,
    allow_deferral: bool = True,
    with_phases: bool = False,
    mcp_provider: Any = None,
) -> tuple[RuntimeToolExecutor, AgentToolset, AgenticTurnRuntime]:
    ws = ConversationWorkspace(tmp_path, "conv-g3")
    (ws.root / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    toolset = AgentToolset(
        ws,
        code_mode_active=True,
        public_tool_names=True,
        permission_mode="full_autonomy",
        run_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        retrieval=object(),
        mcp_provider=mcp_provider,
    )
    store = FakeStore()
    store.turn["conversation_mode"] = "code"
    store.turn["code_mode"] = "code"
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=200_000),
    )
    if with_phases:
        runtime.phases = PhaseController()
        runtime._pending_verification = None
        schemas: list[dict[str, object]] | None = list(runtime.toolset.schemas())
    else:
        runtime.phases = None
        schemas = None
    executor = RuntimeToolExecutor(
        runtime=runtime,
        turn=store.turn,
        tool_schemas=schemas,
        allow_deferral=allow_deferral,
    )
    return executor, toolset, runtime


def test_t11_parallel_tool_batch_collector_preserves_tools_changed_across_concurrent_calls(
    tmp_path: Path,
) -> None:
    """Task 11.3b: When multiple read-only tools run in the same `ToolBatchCollector` batch
    (e.g., `ToolSearch(query='select:WebFetch')` and `Read(path='hello.py')`), `tools_changed=True`
    must be preserved so the MCP server emits `notifications/tools/list_changed`.
    """
    executor, _, _ = _make_native_executor(tmp_path)
    executor.note_message_id("msg-parallel-1")
    executor.register_tool_use_started("u-search", "mcp__orin__ToolSearch", message_id="msg-parallel-1")
    executor.register_tool_use_started("u-read", "mcp__orin__Read", message_id="msg-parallel-1")

    req_search = ToolCallRequest(
        name="ToolSearch",
        arguments={"query": "select:WebFetch"},
        meta={"claudecode/toolUseId": "u-search"},
    )
    req_read = ToolCallRequest(
        name="Read",
        arguments={"path": "hello.py"},
        meta={"claudecode/toolUseId": "u-read"},
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(executor.call_tool, req_search)
        f2 = pool.submit(executor.call_tool, req_read)
        res_search = f1.result(timeout=5)
        res_read = f2.result(timeout=5)

    assert not res_search.is_error
    assert not res_read.is_error
    assert res_search.tools_changed or res_read.tools_changed
    listed_names = {spec.name for spec in executor.list_tools()}
    assert "WebFetch" in listed_names


def test_t11_parallel_mixed_read_and_write_calls_do_not_lose_tools_changed(
    tmp_path: Path,
) -> None:
    """Task 11.3c: When a read-only `ToolSearch` and a non-read-only `Bash` (which triggers
    `FOLLOW_UP_TOOLS`) are emitted in the same assistant message, both discoveries must succeed
    and signal `tools_changed=True`.
    """
    executor, _, _ = _make_native_executor(tmp_path)
    executor.note_message_id("msg-mixed-1")
    executor.register_tool_use_started("u-s", "mcp__orin__ToolSearch", message_id="msg-mixed-1")
    executor.register_tool_use_started("u-b", "mcp__orin__Bash", message_id="msg-mixed-1")

    req_search = ToolCallRequest(
        name="ToolSearch",
        arguments={"query": "select:WebFetch"},
        meta={"claudecode/toolUseId": "u-s"},
    )
    req_bash = ToolCallRequest(
        name="Bash",
        arguments={"command": "echo hi"},
        meta={"claudecode/toolUseId": "u-b"},
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_s = pool.submit(executor.call_tool, req_search)
        f_b = pool.submit(executor.call_tool, req_bash)
        res_s = f_s.result(timeout=10)
        res_b = f_b.result(timeout=10)

    assert res_s.tools_changed is True
    assert res_b.tools_changed is True
    names = {spec.name for spec in executor.list_tools()}
    assert {"WebFetch", "TaskOutput", "TaskStop"} <= names


def test_t11_runtime_tool_executor_dynamic_list_tools_after_toolsearch_direct_call_and_followup(
    tmp_path: Path,
) -> None:
    """Task 11.4a: Verify `RuntimeToolExecutor.list_tools()` recalculates dynamically:
    1. Initially contains only the 15 core tools.
    2. After `ToolSearch(query='select:WebFetch')`: `tools_changed=True` and `WebFetch` appears in `list_tools()`.
       Calling `ToolSearch(query='select:WebFetch')` again returns `tools_changed=False`.
    3. After direct call to an unloaded deferred tool (`NotebookEdit`): `tools_changed=True`
       and that tool appears in `list_tools()`.
    4. After `Bash` (`run_command`): `FOLLOW_UP_TOOLS` (`read_process_output` -> `TaskOutput`,
       `stop_process` -> `TaskStop`) are auto-discovered with `tools_changed=True` and appear in `list_tools()`.
    """
    executor, _, _ = _make_native_executor(tmp_path)

    initial_names = {spec.name for spec in executor.list_tools()}
    assert len(initial_names) == 15
    assert "ToolSearch" in initial_names
    assert "WebFetch" not in initial_names
    assert "TaskOutput" not in initial_names
    assert "TaskStop" not in initial_names
    assert "NotebookEdit" not in initial_names

    # 1. ToolSearch(select:WebFetch)
    r1 = executor.call_tool(
        ToolCallRequest(
            name="ToolSearch",
            arguments={"query": "select:WebFetch"},
            meta={"claudecode/toolUseId": "c1"},
        )
    )
    assert r1.tools_changed is True
    names_after_search = {spec.name for spec in executor.list_tools()}
    assert "WebFetch" in names_after_search

    # Idempotent repeat of ToolSearch(select:WebFetch) -> tools_changed=False
    r1_repeat = executor.call_tool(
        ToolCallRequest(
            name="ToolSearch",
            arguments={"query": "select:WebFetch"},
            meta={"claudecode/toolUseId": "c1b"},
        )
    )
    assert r1_repeat.tools_changed is False

    # 2. Direct call to a deferred tool not yet in `list_tools()` (e.g. `NotebookEdit`)
    assert executor.is_known_tool("NotebookEdit") is True
    r2 = executor.call_tool(
        ToolCallRequest(
            name="NotebookEdit",
            arguments={"notebook_path": "missing.ipynb", "new_source": "x=1"},
            meta={"claudecode/toolUseId": "c2"},
        )
    )
    assert r2.tools_changed is True
    names_after_direct = {spec.name for spec in executor.list_tools()}
    assert "NotebookEdit" in names_after_direct

    # 3. FOLLOW_UP_TOOLS: calling `Bash` (`run_command`) discovers `TaskOutput` and `TaskStop`
    r3 = executor.call_tool(
        ToolCallRequest(
            name="Bash",
            arguments={"command": "echo followup"},
            meta={"claudecode/toolUseId": "c3"},
        )
    )
    assert r3.tools_changed is True
    names_after_bash = {spec.name for spec in executor.list_tools()}
    assert "TaskOutput" in names_after_bash
    assert "TaskStop" in names_after_bash


def test_t11_runtime_tool_executor_list_tools_includes_activated_mcp_tool_when_phases_enabled(
    tmp_path: Path,
) -> None:
    """Task 11.4b (BUG REPRO): When `runtime.phases` is enabled (`PhaseController`) and `ToolSearch`
    activates a deferred remote MCP tool (`mcp__github__create_issue`), `call_tool` returns
    `tools_changed=True` (emitting `notifications/tools/list_changed`), so `executor.list_tools()`
    MUST include `mcp__github__create_issue` when the CLI re-requests `tools/list`!

    Bug in `src/agentos/agentic/native_turn.py:469,825,1551`:
    When `self.runtime.phases is not None`:
    1. `NativeTurnRunner` initializes `RuntimeToolExecutor` with `self.runtime.toolset.schemas()`,
       which filters out remote MCP tools whose names are not yet in `toolset._active_mcp_tool_names`.
    2. When `ToolSearch` adds `mcp__github__create_issue` to `active_mcp_tool_names`, `tools_changed`
       is `True`, yet `_execute_batch` (line 825) and `list_tools()` (line 469) gate `_rebuild_specs`
       on `and self.runtime.phases is None`! Thus `list_tools()` NEVER updates and the CLI never sees
       the activated MCP tool in `tools/list`!
    """
    class _FakeRemoteMcp:
        def definitions(self) -> tuple[ToolDefinition, ...]:
            return (
                ToolDefinition(
                    "mcp__github__create_issue",
                    "Create a GitHub issue on remote repository",
                    {"type": "object", "properties": {"title": {"type": "string"}}},
                    lambda **_: {"content": "issue-created"},
                    "mcp",
                ),
            )

    executor, _, _ = _make_native_executor(
        tmp_path,
        allow_deferral=True,
        with_phases=True,
        mcp_provider=_FakeRemoteMcp(),
    )
    assert "mcp__github__create_issue" not in {s.name for s in executor.list_tools()}

    res = executor.call_tool(
        ToolCallRequest(
            name="ToolSearch",
            arguments={"query": "select:mcp__github__create_issue"},
            meta={"claudecode/toolUseId": "c-mcp"},
        )
    )
    assert res.tools_changed is True
    updated_names = {s.name for s in executor.list_tools()}
    assert "mcp__github__create_issue" in updated_names


def test_t11_native_turn_runner_disables_deferral_when_adapter_lacks_tool_list_changed(
    tmp_path: Path,
) -> None:
    """Task 11.5: If `adapter.capabilities.tool_list_changed` is `False`, `NativeTurnRunner`
    must pass `allow_deferral=False` so `list_tools()` publishes all code-mode tools from turn start.
    """
    ws = ConversationWorkspace(tmp_path, "conv-no-list-changed")
    toolset = AgentToolset(
        ws,
        code_mode_active=True,
        public_tool_names=True,
        permission_mode="full_autonomy",
        run_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        retrieval=object(),
    )
    store = FakeStore()
    store.turn["conversation_mode"] = "code"
    store.turn["code_mode"] = "code"

    captured_tool_names: list[set[str]] = []

    class _NoListChangedAdapter(ClaudeCliAdapter):
        capabilities = replace(ClaudeCliAdapter.capabilities, tool_list_changed=False)

    def _handle_factory(req: NativeTurnRequest) -> ScriptedHandle:
        names = {spec.name for spec in req.executor.list_tools()}
        captured_tool_names.append(names)

        def _round(
            r: NativeTurnRequest,
            sink: NativeTurnSink,
            h: ScriptedHandle,
        ) -> NativeTurnOutcome:
            init = _make_init(r.model)
            sink.on_init(init)
            sink.on_text("All tools loaded upfront.")
            return NativeTurnOutcome(
                result=_make_result(text="All tools loaded upfront."),
                init=init,
                final_text="All tools loaded upfront.",
                exit_code=0,
            )

        return ScriptedHandle(req, [_round])

    provider = FakeNativeProvider(handle_factory=_handle_factory)
    provider.adapter = _NoListChangedAdapter()  # type: ignore[attr-defined]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=200_000),
    )
    runtime.run("t1")

    assert len(captured_tool_names) == 1
    published = captured_tool_names[0]
    assert len(published) > 15
    assert {"WebFetch", "TaskOutput", "TaskStop", "NotebookEdit"} <= published


# ============================================================================
# SURFACE 7: Phase 2 Regressions (Isolation, apiKeySource, MCP_TOOL_TIMEOUT, Idle Timeout)
# ============================================================================


def test_s7_cli_isolation_flags_and_forbidden_flags_rejected(tmp_path: Path) -> None:
    """Surface 7.1: CLI command always includes `--restricted`, `--strict-mcp-config`,
    `--no-session-persistence`, `--tools ""`, `--disable-slash-commands`, `--setting-sources user`,
    and NEVER includes `--bare`, `--safe-mode`, `--session-id`, `--resume`.
    """
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("system", encoding="utf-8")
    mcp_file = tmp_path / "mcp.json"
    mcp_file.write_text("{}", encoding="utf-8")

    cmd = build_claude_command(
        binary="C:/fake/claude.exe",
        system_prompt_file=prompt_file,
        mcp_config_file=mcp_file,
        model="claude-opus-4-7",
    )
    assert "--restricted" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    assert "--disable-slash-commands" in cmd
    assert not (FORBIDDEN_FLAGS & set(cmd))
    for bad_flag in ("--bare", "--safe-mode", "--session-id", "--resume"):
        assert bad_flag not in cmd

    # Attempted session persistence / resume injection through kwargs must raise ValueError
    for bad_kw in (
        {"session_id": "1"},
        {"resume_session_id": "1"},
        {"resume": "1"},
        {"persist_session": True},
    ):
        with pytest.raises(ValueError):
            build_claude_command(binary="C:/fake/claude.exe", system_prompt_file=prompt_file, **bad_kw)

    for bad_model in ("--bare", "--safe-mode", "--session-id", "--resume"):
        with pytest.raises(ValueError):
            build_claude_command(binary="C:/fake/claude.exe", system_prompt_file=prompt_file, model=bad_model)


@pytest.mark.parametrize("bad_source", ["ANTHROPIC_API_KEY", "apiKeyHelper", "oauth", "", "   ", "null"])
def test_s7_api_key_source_not_none_aborts_with_cli_security_abort(bad_source: str) -> None:
    """Surface 7.2: Both `NativeTurnHandle` (`CliSecurityAbort`) and `ClaudeCliProvider`
    (`ProviderTransportError`) abort immediately when `apiKeySource != "none"`.
    """
    lines = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s-sec",
                "model": "claude-opus-4-7",
                "apiKeySource": bad_source,
                "mcp_servers": [{"name": "orin", "status": "connected"}],
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "should never leak",
                "stop_reason": "end_turn",
                "num_turns": 1,
                "duration_ms": 1,
                "session_id": "s-sec",
            }
        ),
    ]
    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-4-7",
        binary="C:/fake/claude.exe",
        popen_factory=lambda cmd, **kw: FakeClaudePopen(cmd, ndjson_lines=lines, env=kw.get("env")),
    )
    try:
        with pytest.raises(ProviderTransportError, match="apiKeySource"):
            list(provider.stream({"messages": [{"role": "user", "content": "hi"}]}))
    finally:
        provider.close()


def test_s7_mcp_tool_timeout_and_idle_timeout_constants_and_enforcement() -> None:
    """Surface 7.3: `MCP_TOOL_TIMEOUT` is stripped from the isolated CLI environment (case-insensitively)
    when no explicit `mcp_tool_timeout_ms` is given, and `IDLE_TIMEOUT_SECONDS` (180s) is enforced.
    """
    env = build_isolated_claude_env(
        {
            "MCP_TOOL_TIMEOUT": "5000",
            "mcp_tool_timeout": "5000",
            "ANTHROPIC_API_KEY": "sk-ant-123",
            "PATH": "/usr/bin",
        }
    )
    lowered_keys = {k.lower() for k in env}
    assert "mcp_tool_timeout" not in lowered_keys
    assert "anthropic_api_key" not in lowered_keys
    assert IDLE_TIMEOUT_SECONDS == 180

    # When tool_timeout_ms IS provided via adapter.environment, MCP_TOOL_TIMEOUT is set to that controlled value
    adapter = ClaudeCliAdapter()
    env_with_timeout = adapter.environment(
        {"MCP_TOOL_TIMEOUT": "1", "ANTHROPIC_API_KEY": "sk-ant-123"},
        max_output_tokens=4096,
        tool_timeout_ms=120_000,
    )
    assert env_with_timeout["MCP_TOOL_TIMEOUT"] == "120000"
    assert "ANTHROPIC_API_KEY" not in env_with_timeout
