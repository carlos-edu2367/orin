from __future__ import annotations

import atexit
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.providers.claude_cli.command import (
    IDLE_TIMEOUT_SECONDS,
    build_claude_command,
    build_isolated_claude_env,
    claude_cli_version,
    ensure_isolated_cwd,
    is_version_validated,
    merge_with_system_env,
    redact_argv,
    resolve_claude_binary,
    write_owner_only_temp,
)
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
    ProviderTransportError,
    RateLimitState,
    classify_result_error,
    error_info_to_transport_error,
)
from agentos.providers.cli_adapter import CliAgentAdapter, CliTurnRequest
from agentos.providers.cli_queue import CliSlotQueue, CliSlotTicket, cli_slot_queue
from agentos.providers.claude_cli.stream import (
    CliEvent,
    CliEventKind,
    CliInit,
    CliResult,
    StreamJsonParser,
)
from agentos.providers.claude_cli.text_protocol import (
    TextProtocolEvent,
    ToolCallStreamParser,
    TranscriptState,
    build_system_prompt,
    extract_tool_declarations,
    resolve_native_tool_call,
)
from agentos.providers.models import FinishReason

_LOGGER = logging.getLogger("agentos.providers.claude_cli")

DEFAULT_CLAUDE_CLI_BRIDGE_URL = "http://127.0.0.1:18447"
CLAUDE_CODE_LOCAL_BASE_URL = DEFAULT_CLAUDE_CLI_BRIDGE_URL
ANTHROPIC_CLOUD_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_BASE_URL = ANTHROPIC_CLOUD_BASE_URL

_CLOUD_HOSTS = frozenset({"api.anthropic.com", "anthropic.com"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

TEXT_CACHE_OK: bool = True
NATIVE_LOOP_VALIDATED: bool = True
NATIVE_MODES: frozenset[str] = frozenset({"code", "chat", "friend"})
_FALLBACK_TEXT_BUDGET = 200_000
_MIN_TEXT_BUDGET = 50_000
_CIRCUIT_BREAKER_SECONDS: float = 600.0
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT_BREAKER_UNTIL: dict[str, float] = {}
_WARNED_INVALID_NATIVE_FLAGS: set[str] = set()


def clear_circuit_breaker() -> None:
    """Reset the native loop circuit breaker state (used in tests)."""
    with _CIRCUIT_LOCK:
        _CIRCUIT_BREAKER_UNTIL.clear()
        _WARNED_INVALID_NATIVE_FLAGS.clear()


def record_native_unavailable(
    binary: str,
    *,
    now: float | None = None,
    reason: object = "",
) -> None:
    """Open the circuit breaker for `binary` for 10 minutes (logged once per open period)."""
    key = str(binary or "claude")
    ts = time.monotonic() if now is None else float(now)
    until = ts + _CIRCUIT_BREAKER_SECONDS
    already_open = False
    with _CIRCUIT_LOCK:
        prev_until = _CIRCUIT_BREAKER_UNTIL.get(key, 0.0)
        if prev_until > ts:
            already_open = True
        else:
            _CIRCUIT_BREAKER_UNTIL[key] = until
    if not already_open:
        _LOGGER.warning(
            "claude_cli native loop circuit breaker opened for %.0fs on binary=%s: %s",
            _CIRCUIT_BREAKER_SECONDS,
            key,
            reason,
        )


def _is_circuit_open(binary: str, *, now: float | None = None) -> bool:
    key = str(binary or "claude")
    ts = time.monotonic() if now is None else float(now)
    with _CIRCUIT_LOCK:
        until = _CIRCUIT_BREAKER_UNTIL.get(key)
        if until is None:
            return False
        if ts >= until:
            _CIRCUIT_BREAKER_UNTIL.pop(key, None)
            return False
        return True


def text_context_budget(env: Mapping[str, str] | None = None) -> int | None:
    """Return an explicit context token budget for the text protocol only when configured or cache is unverified."""
    source = os.environ if env is None else env
    raw = str(source.get("ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET") or "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return max(_MIN_TEXT_BUDGET, parsed)
        except ValueError:
            pass
    if not TEXT_CACHE_OK:
        return _FALLBACK_TEXT_BUDGET
    return None


def is_code_mode_value(value: object) -> bool:
    """Return True only when `value` is boolean `True` or string `'code'` (never `'off'` or `'chat'`)."""
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() == "code"


def native_tools_enabled(
    *,
    conversation_mode: str | None = None,
    code_mode: bool | str = False,
    mode: str | None = None,
    env: Mapping[str, str] | None = None,
    version: tuple[int, int, int] | None = None,
) -> bool:
    """Return True when the native MCP tool loop is enabled for the given mode and CLI version."""
    source = os.environ if env is None else env
    raw_flag = str(source.get("ORIN_CLAUDE_CLI_NATIVE_TOOLS") or "").strip().lower()
    if raw_flag in {"off", "0", "false", "no", "disabled"}:
        return False
    if raw_flag not in {"", "auto", "on", "1", "true", "yes"}:
        with _CIRCUIT_LOCK:
            should_warn = raw_flag not in _WARNED_INVALID_NATIVE_FLAGS
            if should_warn:
                _WARNED_INVALID_NATIVE_FLAGS.add(raw_flag)
        if should_warn:
            _LOGGER.warning(
                "Invalid ORIN_CLAUDE_CLI_NATIVE_TOOLS=%r; defaulting to 'auto'",
                raw_flag,
            )

    if not NATIVE_LOOP_VALIDATED:
        return False
    if not is_version_validated(version):
        return False

    if is_code_mode_value(code_mode):
        effective_mode = "code"
    elif mode is not None and str(mode).strip():
        effective_mode = str(mode).strip().lower()
    else:
        effective_mode = str(conversation_mode or "").strip().lower()
    return effective_mode in NATIVE_MODES


def normalize_anthropic_base_url(value: str) -> str:
    """Validate and normalize an Anthropic Local CLI Bridge or Cloud API URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Anthropic base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Anthropic base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Anthropic base URL must not contain credentials, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Anthropic base URL has an invalid port") from exc

    host = (parsed.hostname or "").lower()
    is_cloud = host in _CLOUD_HOSTS
    is_loopback = host in _LOOPBACK_HOSTS
    if not is_cloud and not is_loopback:
        raise ValueError(
            "Anthropic base URL must be a local loopback address or api.anthropic.com"
        )
    if is_cloud:
        if parsed.scheme != "https":
            raise ValueError("Anthropic Cloud base URL must use HTTPS")
        return ANTHROPIC_CLOUD_BASE_URL

    path = parsed.path.rstrip("/")
    for suffix in ("/v1/models", "/v1/messages", "/v1", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def is_claude_cli_bridge_url(base_url: str | None) -> bool:
    """Return True when the URL points to the local Claude Code CLI bridge."""
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    try:
        normalized = normalize_anthropic_base_url(base_url)
    except ValueError:
        return False
    host = (urlsplit(normalized).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def is_anthropic_cloud(base_url: str | None) -> bool:
    """Return True when the URL points to the hosted Anthropic Console API."""
    return not is_claude_cli_bridge_url(base_url)


CliSlots = CliSlotQueue


def kill_process_tree(proc: Any) -> None:
    """Terminate the subprocess and any child processes without leaving orphans."""
    facade = sys.modules.get("agentos.provider_catalog.claude_code")
    patched_kill = (
        getattr(facade, "_kill_process_tree", None) if facade is not None else None
    )
    if callable(patched_kill) and patched_kill is not kill_process_tree:
        patched_kill(proc)
        return

    pid = getattr(proc, "pid", None)
    if os.name == "nt" and isinstance(pid, int) and pid > 0:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                creationflags=creationflags,
                check=False,
            )
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def log_cli_run(
    *,
    argv: Sequence[str],
    requested_model: str,
    init: CliInit | None,
    result: CliResult | None,
    exit_code: int | None,
    duration_s: float,
    stderr_tail: str,
    mode: Literal["text", "native"],
    usage: Any = None,
) -> None:
    """Log one structured diagnostic line per `claude -p` execution with redacted paths."""
    redacted_argv = redact_argv(argv)
    actual_model = (
        (init.model if init is not None and init.model else None)
        or requested_model
        or "default"
    )
    effective_usage = usage if usage is not None else (result.usage if result is not None else None)
    in_tok = effective_usage.input_tokens if effective_usage is not None else 0
    out_tok = effective_usage.output_tokens if effective_usage is not None else 0
    cache_read = effective_usage.cache_read_input_tokens if effective_usage is not None else 0
    cache_creation = effective_usage.cache_creation_input_tokens if effective_usage is not None else 0

    lines = (stderr_tail or "").splitlines()[-20:]
    tail = "\n".join(lines)
    if len(tail) > 2000:
        tail = tail[-2000:]

    _LOGGER.info(
        "claude_cli run mode=%s argv=%s requested=%s actual=%s exit=%s duration=%.1fs in=%d out=%d cache_read=%d cache_creation=%d stderr_tail=%r",
        mode,
        redacted_argv,
        requested_model or "default",
        actual_model,
        exit_code,
        duration_s,
        in_tok,
        out_tok,
        cache_read,
        cache_creation,
        tail,
    )


def _resolve_binary_for_provider(
    explicit_binary: str | None, env: Mapping[str, str] | None
) -> str | None:
    if explicit_binary:
        return explicit_binary
    facade = sys.modules.get("agentos.provider_catalog.claude_code")
    patched = (
        getattr(facade, "_resolve_claude_binary", None) if facade is not None else None
    )
    if callable(patched) and patched is not resolve_claude_binary:
        return patched(env)
    return resolve_claude_binary(env)


def _extract_stable_system_text(messages: Sequence[Mapping[str, Any]]) -> str:
    if not messages:
        return "You are a helpful assistant."
    first = messages[0]
    if str(first.get("role") or "").strip().lower() != "system":
        return "You are a helpful assistant."
    raw = first.get("content")
    if isinstance(raw, str):
        return raw.strip() or "You are a helpful assistant."
    if isinstance(raw, list):
        parts = [
            str(p.get("text") or p.get("content") or "")
            for p in raw
            if isinstance(p, Mapping) and (p.get("text") or p.get("content"))
        ]
        joined = "\n".join(parts).strip()
        return joined or "You are a helpful assistant."
    return str(raw or "").strip() or "You are a helpful assistant."


class ClaudeCliProvider:
    """First-class Orin provider for the local `claude -p` subscription."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model: str = "",
        binary: str | None = None,
        popen_factory: Callable[..., Any] | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        slots: CliSlots | None = None,
        adapter: CliAgentAdapter | None = None,
    ) -> None:
        self.adapter: CliAgentAdapter = (
            adapter if adapter is not None else ClaudeCliAdapter()
        )
        self.base_url = normalize_anthropic_base_url(
            base_url or DEFAULT_CLAUDE_CLI_BRIDGE_URL
        )
        self.model = str(model or "")
        self._binary = binary
        self._popen_factory = popen_factory
        self._env = dict(env) if env is not None else None
        self._clock = clock
        self._slots = slots
        self._transcript = TranscriptState()
        self._union_declarations: list[dict[str, Any]] = []
        self._union_tool_names: set[str] = set()
        self._prompt_file_path: Path | None = None
        self._last_system_prompt: str | None = None
        atexit.register(self.close)

    def _ensure_prompt_file(self, system_prompt: str) -> Path:
        content = system_prompt or "You are a helpful assistant."
        if (
            self._prompt_file_path is not None
            and self._prompt_file_path.exists()
        ):
            if content != self._last_system_prompt:
                self._prompt_file_path.write_text(content, encoding="utf-8")
                self._last_system_prompt = content
            return self._prompt_file_path

        self._prompt_file_path = write_owner_only_temp(
            content,
            prefix="orin-claude-sys-",
            suffix=".prompt.txt",
        )
        self._last_system_prompt = content
        return self._prompt_file_path

    def _resolve_binary(self) -> str | None:
        if self._binary:
            return self._binary
        facade = sys.modules.get("agentos.provider_catalog.claude_code")
        patched = (
            getattr(facade, "_resolve_claude_binary", None)
            if facade is not None
            else None
        )
        if callable(patched) and patched is not resolve_claude_binary:
            return patched(self._env)
        binary, _ = self.adapter.resolve_binary(self._env)
        return binary

    def _effective_slots(self) -> CliSlotQueue:
        return self._slots if self._slots is not None else cli_slot_queue()

    def record_native_unavailable(self, reason: object = "") -> None:
        if getattr(reason, "status_code", None) == 499:
            return
        binary = self._resolve_binary() or self._binary or "claude"
        record_native_unavailable(binary, now=self._clock(), reason=reason)

    def native_turn_available(self, turn: Mapping[str, object]) -> bool:
        if not self.adapter.capabilities.native_tool_loop:
            return False
        if self._binary:
            binary: str | None = self._binary
            version = claude_cli_version(binary, env=self._env)
        else:
            binary, version = self.adapter.resolve_binary(self._env)
        if not binary:
            return False
        if _is_circuit_open(binary, now=self._clock()):
            return False
        if not self.adapter.version_supported(version):
            return False
        return native_tools_enabled(
            conversation_mode=str(turn.get("conversation_mode") or ""),
            code_mode=is_code_mode_value(turn.get("code_mode")),
            env=self._env,
            version=version,
        )

    def start_native_turn(self, request: Any) -> Any:
        from agentos.providers.claude_cli.native_loop import (
            start_native_turn_for_provider,
        )

        try:
            return start_native_turn_for_provider(self, request)
        except NativeLoopUnavailable as exc:
            if getattr(exc, "status_code", None) != 499:
                self.record_native_unavailable(exc)
            raise

    def close(self) -> None:
        try:
            atexit.unregister(self.close)
        except Exception:
            pass
        if self._prompt_file_path is not None:
            try:
                self._prompt_file_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._prompt_file_path = None
            self._last_system_prompt = None
        self._union_declarations.clear()
        self._union_tool_names.clear()
        self._transcript = TranscriptState()

    def stream(
        self,
        request: Mapping[str, object],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[NormalizedStreamItem]:
        req_model = str(request.get("model") or self.model or "").strip()
        raw_messages = request.get("messages")
        messages: list[Mapping[str, Any]] = (
            [m for m in raw_messages if isinstance(m, Mapping)]
            if isinstance(raw_messages, Iterable)
            else []
        )
        raw_tools = request.get("tools")
        tools: list[Mapping[str, Any]] = (
            [t for t in raw_tools if isinstance(t, Mapping)]
            if isinstance(raw_tools, Iterable)
            else []
        )
        tool_choice = request.get("tool_choice")
        effort = (
            str(request["effort"]).strip()
            if request.get("effort") is not None and str(request.get("effort")).strip()
            else None
        )
        max_output_tokens_raw = request.get("max_output_tokens")
        max_output_tokens: int | None = None
        if (
            isinstance(max_output_tokens_raw, int)
            and not isinstance(max_output_tokens_raw, bool)
            and max_output_tokens_raw > 0
        ):
            max_output_tokens = max_output_tokens_raw

        effective_cancel = cancel_check or (
            request.get("cancel_check")
            if callable(request.get("cancel_check"))
            else None
        )

        idle_timeout_raw = (
            request.get("idle_timeout_seconds")
            if request.get("idle_timeout_seconds") is not None
            else (
                request.get("timeout")
                if request.get("timeout") is not None
                else request.get("timeout_seconds")
            )
        )
        effective_timeout = (
            float(idle_timeout_raw)  # type: ignore[arg-type]
            if isinstance(idle_timeout_raw, (int, float))
            and not isinstance(idle_timeout_raw, bool)
            else float(IDLE_TIMEOUT_SECONDS)
        )
        max_wall_raw = request.get("max_wall_clock_seconds")
        max_wall_clock = (
            float(max_wall_raw)  # type: ignore[arg-type]
            if isinstance(max_wall_raw, (int, float))
            and not isinstance(max_wall_raw, bool)
            else max(effective_timeout * 10.0, 1800.0)
        )

        resolved_binary = self._resolve_binary()
        if not resolved_binary:
            raise ProviderTransportError(
                "Claude Code CLI executable ('claude') was not found on this machine."
            )

        withheld = isinstance(tool_choice, str) and tool_choice.lower() == "none"
        all_decls, all_names = extract_tool_declarations(tools)
        for decl in all_decls:
            d_name = str(decl.get("name") or "")
            if d_name and d_name not in self._union_tool_names:
                self._union_tool_names.add(d_name)
                self._union_declarations.append(decl)

        allowed_now: set[str] = set() if withheld else set(all_names)
        stable_system = _extract_stable_system_text(messages)
        system_prompt = build_system_prompt(stable_system, self._union_declarations)

        has_tools_in_turn = bool(self._union_declarations)
        content_blocks = self._transcript.blocks_for(
            messages,
            allowed_now=allowed_now if (has_tools_in_turn and not withheld) else None,
            closing=withheld and has_tools_in_turn,
        )

        slots = self._effective_slots()
        conv_id = str(request.get("conversation_id") or request.get("turn_id") or "")
        ticket: CliSlotTicket | None = None
        proc: Any = None
        cmd: list[str] = []
        stderr_chunks: list[str] = []
        stdout_thread: threading.Thread | None = None
        stderr_thread: threading.Thread | None = None
        parser = self.adapter.stream_translator()
        mono_started = time.monotonic()
        _ = self._clock()
        sequence = 0

        try:
            if effective_cancel is not None and effective_cancel():
                return
            ticket = slots.enqueue(conv_id)
            while not ticket.wait(0.0):
                if effective_cancel is not None and effective_cancel():
                    ticket.release()
                    ticket = None
                    return
                sequence += 1
                yield NormalizedStreamItem(
                    StreamKind.WAITING,
                    sequence,
                    queue_position=ticket.position(),
                    queue_limit=slots.limit,
                    wait_reason=ticket.wait_reason(),
                )
                granted = False
                for _ in range(10):
                    if effective_cancel is not None and effective_cancel():
                        ticket.release()
                        ticket = None
                        return
                    if ticket.wait(0.05):
                        granted = True
                        break
                if granted:
                    break
            mono_started = time.monotonic()

            prompt_file = self._ensure_prompt_file(system_prompt)
            facade = sys.modules.get("agentos.provider_catalog.claude_code")
            patched_build = (
                getattr(facade, "_build_claude_cli_command", None)
                if facade is not None
                else None
            )
            if callable(patched_build) and patched_build is not build_claude_command:
                cmd = list(
                    patched_build(
                        binary=resolved_binary,
                        system_prompt_file=prompt_file,
                        model=req_model or None,
                        effort=effort,
                        max_turns=1,
                    )
                )
            else:
                cmd = self.adapter.build_command(
                    CliTurnRequest(
                        binary=resolved_binary,
                        system_prompt_file=prompt_file,
                        model=req_model or None,
                        effort=effort,
                        max_turns=1,
                    )
                )
            isolated_env = self.adapter.environment(
                merge_with_system_env(self._env),
                max_output_tokens=max_output_tokens,
                tool_timeout_ms=None,
            )
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            safe_cwd = str(ensure_isolated_cwd())

            if self._popen_factory is not None:
                try:
                    proc = self._popen_factory(
                        cmd,
                        cwd=safe_cwd,
                        env=isolated_env,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                except TypeError:
                    proc = self._popen_factory(
                        cmd,
                        env=isolated_env,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
            else:
                proc = subprocess.Popen(
                    cmd,
                    cwd=safe_cwd,
                    env=isolated_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )

            line_queue: queue.Queue[str | None] = queue.Queue()

            def _read_stdout() -> None:
                try:
                    stdout_stream = getattr(proc, "stdout", None)
                    if stdout_stream is not None:
                        for raw_line in stdout_stream:
                            line_queue.put(str(raw_line))
                except Exception:
                    pass
                finally:
                    line_queue.put(None)

            def _read_stderr() -> None:
                try:
                    stderr_stream = getattr(proc, "stderr", None)
                    if stderr_stream is not None:
                        data = stderr_stream.read()
                        if data:
                            stderr_chunks.append(str(data))
                except Exception:
                    pass

            stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            user_envelope = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": content_blocks,
                },
            }
            if getattr(proc, "stdin", None) is not None:
                try:
                    proc.stdin.write(
                        json.dumps(user_envelope, ensure_ascii=False) + "\n"
                    )
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.stdin.close()
                except Exception:
                    pass

            saw_stream_thinking = False
            saw_stream_text = False
            emitted_any_tool_call = False
            last_empty_thinking_at: float | None = None
            tool_parser = ToolCallStreamParser(allowed_names=allowed_now)

            hard_deadline = mono_started + max_wall_clock
            deadline = min(hard_deadline, mono_started + effective_timeout)

            def _handle_tp_events(
                tp_events: Sequence[TextProtocolEvent],
            ) -> Iterator[NormalizedStreamItem]:
                nonlocal sequence, emitted_any_tool_call
                for tp_ev in tp_events:
                    if tp_ev.kind == "text" and tp_ev.text:
                        sequence += 1
                        yield NormalizedStreamItem(
                            StreamKind.TEXT, sequence, text=tp_ev.text
                        )
                    elif tp_ev.kind == "tool_call":
                        emitted_any_tool_call = True
                        sequence += 1
                        yield NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            sequence,
                            tool_call_id=tp_ev.call_id,
                            tool_name=tp_ev.name,
                            arguments_delta=tp_ev.arguments,
                        )
                    elif tp_ev.kind == "stop":
                        kill_process_tree(proc)

            while True:
                if effective_cancel is not None and effective_cancel():
                    kill_process_tree(proc)
                    return

                if time.monotonic() >= deadline:
                    kill_process_tree(proc)
                    raise ProviderTransportError(
                        f"Claude Code CLI timed out after {effective_timeout:.1f}s"
                    )

                try:
                    raw_line = line_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if raw_line is None:
                    break

                deadline = min(
                    hard_deadline, time.monotonic() + effective_timeout
                )
                cli_events = parser.feed_line(raw_line)
                should_break = False

                for ev in cli_events:
                    if ev.kind == CliEventKind.INIT:
                        if parser.init is not None:
                            api_key_source = parser.init.api_key_source.strip()
                            if api_key_source.lower() != "none":
                                kill_process_tree(proc)
                                raise CliSecurityAbort(
                                    f"Security abort: Claude CLI reported apiKeySource={api_key_source!r} instead of 'none'. "
                                    "Refusing to run to prevent unexpected API token charges; only OAuth subscription is permitted."
                                )
                    elif ev.kind == CliEventKind.ERROR:
                        kill_process_tree(proc)
                        raise ProviderTransportError(
                            ev.text or "Claude CLI error"
                        )
                    elif parser.init is None and not (
                        ev.kind == CliEventKind.RATE_LIMIT
                        or (
                            ev.kind == CliEventKind.RESULT
                            and parser.result is not None
                            and parser.result.is_error
                        )
                    ):
                        # Nothing visible (text, thinking, tools, usage or a successful result) counts before
                        # the init proved the run is on the subscription; an error result is classified below.
                        kill_process_tree(proc)
                        raise CliSecurityAbort(
                            f"Security abort: Claude CLI emitted {ev.kind.value!r} before system/init packet."
                        )
                    elif ev.kind == CliEventKind.THINKING_DELTA:
                        if ev.text:
                            saw_stream_thinking = True
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.THINKING,
                                sequence,
                                thinking=ev.text,
                            )
                        else:
                            now_th = self._clock()
                            if (
                                last_empty_thinking_at is None
                                or (now_th - last_empty_thinking_at) >= 2.0
                            ):
                                last_empty_thinking_at = now_th
                                sequence += 1
                                yield NormalizedStreamItem(
                                    StreamKind.THINKING,
                                    sequence,
                                    thinking="",
                                )
                    elif ev.kind == CliEventKind.THINKING_SIGNATURE:
                        if ev.text:
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.THINKING,
                                sequence,
                                signature=ev.text,
                            )
                    elif ev.kind == CliEventKind.TEXT_DELTA:
                        if ev.text:
                            saw_stream_text = True
                            yield from _handle_tp_events(
                                tool_parser.feed(ev.text)
                            )
                            if tool_parser.stopped:
                                should_break = True
                                break
                    elif ev.kind == CliEventKind.TOOL_USE_END and not withheld:
                        resolved = resolve_native_tool_call(
                            ev.tool_name or "",
                            ev.tool_input or {},
                            allowed_now,
                        )
                        if resolved is not None:
                            mapped_name, args_json = resolved
                            emitted_any_tool_call = True
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.TOOL_CALL,
                                sequence,
                                tool_call_id=ev.tool_use_id or "",
                                tool_name=mapped_name,
                                arguments_delta=args_json,
                            )
                    elif ev.kind == CliEventKind.RESULT:
                        if parser.result is not None and parser.result.is_error:
                            err_info = self.adapter.classify_error(
                                parser.result, parser.rate_limit
                            )
                            raise error_info_to_transport_error(err_info)
                        if (
                            not saw_stream_text
                            and parser.result is not None
                            and parser.result.result_text.strip()
                        ):
                            saw_stream_text = True
                            yield from _handle_tp_events(
                                tool_parser.feed(parser.result.result_text)
                            )
                        should_break = True
                        break

                if should_break:
                    break

            for pending_ev in parser.flush_pending():
                if pending_ev.kind == CliEventKind.TOOL_USE_END and not withheld:
                    resolved = resolve_native_tool_call(
                        pending_ev.tool_name or "",
                        pending_ev.tool_input or {},
                        allowed_now,
                    )
                    if resolved is not None:
                        mapped_name, args_json = resolved
                        emitted_any_tool_call = True
                        sequence += 1
                        yield NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            sequence,
                            tool_call_id=pending_ev.tool_use_id or "",
                            tool_name=mapped_name,
                            arguments_delta=args_json,
                        )

            yield from _handle_tp_events(tool_parser.finish())

            if effective_cancel is not None and effective_cancel():
                kill_process_tree(proc)
                return

            if parser.result is None and not tool_parser.stopped:
                if stderr_thread is not None:
                    stderr_thread.join(timeout=0.2)
                exit_code = proc.poll()
                stderr_text = "".join(stderr_chunks).strip()
                if (exit_code is not None and exit_code != 0) or (
                    not saw_stream_text
                    and not saw_stream_thinking
                    and not emitted_any_tool_call
                    and stderr_text
                ):
                    raise ProviderTransportError(
                        f"Claude Code CLI exited unexpectedly (code={exit_code}): {stderr_text or 'no output'}"
                    )

            final_usage = parser.latest_usage().to_stream_usage()
            zero_cost = Decimal("0.0")
            sequence += 1
            yield NormalizedStreamItem(
                StreamKind.USAGE,
                sequence,
                usage=final_usage,
                cost=zero_cost,
            )

            sequence += 1
            if emitted_any_tool_call:
                finish_reason = FinishReason.TOOL_CALLS
            elif (
                parser.stop_reason == "max_tokens"
                or tool_parser.saw_unclosed_tool_call
            ):
                finish_reason = FinishReason.LENGTH
            else:
                finish_reason = FinishReason.STOP
            yield NormalizedStreamItem(
                StreamKind.FINISH,
                sequence,
                finish_reason=finish_reason,
                usage=final_usage,
                cost=zero_cost,
            )
        finally:
            exit_code_final: int | None = None
            if proc is not None:
                if proc.poll() is None:
                    kill_process_tree(proc)
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
                for s_attr in ("stdout", "stderr"):
                    s_obj = getattr(proc, s_attr, None)
                    if s_obj is not None:
                        try:
                            s_obj.close()
                        except Exception:
                            pass
                exit_code_final = proc.poll()
            if stdout_thread is not None:
                try:
                    stdout_thread.join(timeout=0.5)
                except Exception:
                    pass
            if stderr_thread is not None:
                try:
                    stderr_thread.join(timeout=0.5)
                except Exception:
                    pass
            if cmd:
                log_cli_run(
                    argv=cmd,
                    requested_model=req_model,
                    init=parser.init,
                    result=parser.result,
                    exit_code=exit_code_final,
                    duration_s=max(0.0, time.monotonic() - mono_started),
                    stderr_tail="".join(stderr_chunks),
                    mode="text",
                )
            if ticket is not None:
                ticket.release()


def stream_claude_code_bridge(
    request: Mapping[str, Any] | None = None,
    *,
    base_url: str = DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    model: str = "",
    messages: Iterable[Mapping[str, Any]] = (),
    tools: Iterable[Mapping[str, Any]] = (),
    tool_choice: object = None,
    effort: str | None = None,
    timeout_seconds: float = float(IDLE_TIMEOUT_SECONDS),
    timeout: float | None = None,
    idle_timeout_seconds: float | None = None,
    max_wall_clock_seconds: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    binary: str | None = None,
    popen_factory: Callable[..., Any] | None = None,
) -> Iterator[NormalizedStreamItem]:
    """Compatibility wrapper around `ClaudeCliProvider(...).stream(...)`."""
    req: dict[str, Any] = dict(request) if isinstance(request, Mapping) else {}
    effective_model = str(model or req.get("model") or "")
    req["model"] = effective_model
    if not req.get("messages"):
        req["messages"] = list(messages)
    if not req.get("tools"):
        req["tools"] = list(tools)
    if tool_choice is not None:
        req["tool_choice"] = tool_choice
    if effort is not None:
        req["effort"] = effort
    if idle_timeout_seconds is not None:
        req["idle_timeout_seconds"] = idle_timeout_seconds
    elif timeout is not None:
        req["timeout"] = timeout
    elif "idle_timeout_seconds" not in req and "timeout" not in req:
        req["timeout_seconds"] = timeout_seconds
    if max_wall_clock_seconds is not None:
        req["max_wall_clock_seconds"] = max_wall_clock_seconds

    provider = ClaudeCliProvider(
        base_url=base_url,
        model=effective_model,
        binary=binary,
        popen_factory=popen_factory,
    )
    try:
        yield from provider.stream(req, cancel_check=cancel_check)
    finally:
        provider.close()
