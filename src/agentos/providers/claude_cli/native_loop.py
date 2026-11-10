from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from agentos.installation.paths import orin_paths
from agentos.providers.claude_cli.command import (
    IDLE_TIMEOUT_SECONDS as _DEFAULT_IDLE_TIMEOUT_SECONDS,
    VALIDATED_CLI_RANGE,
    build_claude_command,
    build_isolated_claude_env,
    claude_cli_version,
    ensure_isolated_cwd,
    is_version_validated,
    merge_with_system_env,
    user_event_line,
    write_owner_only_temp,
)
import tempfile

IDLE_TIMEOUT_SECONDS = _DEFAULT_IDLE_TIMEOUT_SECONDS

from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
    ProviderTransportError,
    RateLimitState,
    classify_result_error,
    error_info_to_transport_error,
)
from agentos.providers.claude_cli.stream import (
    CliEvent,
    CliEventKind,
    CliInit,
    CliResult,
    CliUsage,
    StreamJsonParser,
)
from agentos.providers.cli_adapter import (
    CliAgentAdapter,
    CliStreamTranslator,
    CliTurnRequest,
    ToolServerWiring,
)
from agentos.providers.cli_queue import CliSlotTicket, acquire_cli_slot
from agentos.providers.cli_tools_mcp import (
    BackgroundTurnToolServer,
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
    ToolExecutor,
    TurnToolServer,
)

PERSISTENT_STDIN_OK: bool = True
NATIVE_LOOP_VALIDATED: bool = True


def native_workdir(adapter_name: str = "claude") -> Path:
    slug = str(adapter_name or "claude").strip() or "claude"
    return orin_paths().data / f"{slug}-cli" / "workdir"


def isolated_cli_note(server_name: str) -> str:
    """Tells the model that the CLI's own sandbox/cwd notes do not limit it.

    Orin starts every CLI (Claude, Codex, Antigravity) in an empty folder with the CLI's built-in tools off, so each
    CLI describes that folder, and Codex a read-only sandbox, as the environment. A model that believed it concluded
    it could not change files ("read-only filesystem") although Orin's tools act on the real workspace with the
    turn's permission mode: subagents on Codex and Antigravity never edited anything.
    """
    srv = str(server_name or "orin").strip() or "orin"
    return (
        "# Where you work\n"
        "Orin runs this CLI from an empty, isolated folder with the CLI's own built-in tools (shell, file editing, "
        "apply_patch, its sandbox) turned off. Any environment, sandbox or permission information the CLI itself adds "
        "(current directory, workspace roots, read-only filesystem, permission profile, approval policy) describes "
        "only that isolated folder and those disabled tools: it does not apply to you. Do all the work through the "
        f"tools of the `{srv}` MCP server. They act on the working directory and with the permission mode stated in "
        "these instructions, and they can write and edit files and run commands; use them for that instead of "
        "concluding that you cannot."
    )


def with_isolated_cli_note(system_prompt: str, server_name: str) -> str:
    note = isolated_cli_note(server_name)
    base = str(system_prompt or "").rstrip()
    if note in base:
        return base
    return f"{base}\n\n{note}" if base else note


def find_forbidden_item_kind(
    forbidden: frozenset[str] | set[str] | Sequence[str],
    *,
    raw: Mapping[str, Any] | None = None,
    raw_line: str | None = None,
    tool_name: str | None = None,
) -> str | None:
    if not forbidden:
        return None
    forbidden_set = (
        forbidden
        if isinstance(forbidden, (set, frozenset))
        else frozenset(forbidden)
    )
    payload = raw
    if payload is None and raw_line:
        stripped = raw_line.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, Mapping):
                    payload = parsed
            except Exception:
                payload = None
    candidates: list[object] = []

    def _collect(obj: Any, depth: int = 0) -> None:
        if depth > 6 or not isinstance(obj, Mapping):
            return
        candidates.append(obj.get("type"))
        candidates.append(obj.get("kind"))
        item_obj = obj.get("item")
        if isinstance(item_obj, Mapping):
            candidates.append(item_obj.get("type"))
            candidates.append(item_obj.get("kind"))
        step_obj = obj.get("step_update")
        if isinstance(step_obj, Mapping):
            step_type_raw = str(step_obj.get("step_type") or "").strip()
            candidates.append(step_type_raw)
            tool_info = step_obj.get("tool_info")
            is_tool_step = (
                step_type_raw.lower() == "tool"
                or step_obj.get("tool_name") is not None
                or isinstance(tool_info, Mapping)
            )
            if is_tool_step:
                tname = str(
                    step_obj.get("tool_name")
                    or (
                        tool_info.get("name")
                        if isinstance(tool_info, Mapping)
                        else ""
                    )
                    or ""
                ).strip()
                candidates.append(tname)
                if (
                    "forbidden_builtin_tool" in forbidden_set
                    and step_type_raw.lower()
                    not in ("user_input", "agent_response")
                ):
                    if not tname.startswith(
                        ("mcp_orin_", "orin/", "mcp__orin__")
                    ) or tname in {"mcp_orin_", "orin/", "mcp__orin__"}:
                        candidates.append(
                            tname
                            if tname in forbidden_set
                            else "forbidden_builtin_tool"
                        )
        init_obj = obj.get("init")
        if (
            isinstance(init_obj, Mapping)
            and "forbidden_builtin_tool" in forbidden_set
        ):
            raw_tools = init_obj.get("tools")
            if isinstance(raw_tools, list):
                for item in raw_tools:
                    if not isinstance(item, str) or not item.strip():
                        continue
                    t_str = item.strip()
                    if t_str == "forbidden_builtin_tool":
                        candidates.append("forbidden_builtin_tool")
                    elif (
                        not t_str.startswith(
                            ("mcp_orin_", "orin/", "mcp__orin__")
                        )
                        and t_str not in forbidden_set
                    ):
                        candidates.append("forbidden_builtin_tool")
        for val in obj.values():
            if isinstance(val, Mapping):
                _collect(val, depth + 1)

    if isinstance(payload, Mapping):
        _collect(payload, 0)
    if tool_name:
        candidates.append(tool_name)
    for cand in candidates:
        if isinstance(cand, str) and cand in forbidden_set:
            return cand
    return None


@dataclass(slots=True)
class NativeTurnRequest:
    model: str
    system_prompt: str
    user_content: list[dict[str, Any]]
    executor: ToolExecutor
    cancel_check: Callable[[], bool]
    deadline_monotonic: float
    max_turns: int
    cwd: Path
    effort: str | None = None
    fallback_model: str | None = None
    max_output_tokens: int | None = None
    autocompact: str | int | None = None
    session: Any | None = None
    resume_session_id: str | None = None
    server_name: str = "orin"
    conversation_id: str = ""
    on_waiting_slot: Callable[..., None] | None = None


class NativeTurnSink(Protocol):
    def on_init(self, init: CliInit) -> None: ...
    def on_text(self, text: str) -> None: ...
    def on_thinking(self, text: str) -> None: ...
    def on_tool_use_started(self, tool_use_id: str, tool_name: str) -> None: ...
    def on_tool_input_progress(
        self, tool_use_id: str, tool_name: str, bytes_received: int
    ) -> None: ...
    def on_tool_use_complete(
        self, tool_use_id: str, tool_name: str, tool_input: Mapping[str, Any]
    ) -> None: ...
    def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None: ...
    def on_compacted(self) -> None: ...
    def on_rate_limit(self, state: RateLimitState) -> None: ...
    def on_waiting_model(
        self, seconds: int, *, extended: bool = False
    ) -> None: ...


OUTPUT_TAIL_MAX_LINES = 20
OUTPUT_TAIL_MAX_CHARS = 4096


@dataclass(frozen=True, slots=True)
class NativeTurnOutcome:
    result: CliResult
    init: CliInit | None
    final_text: str
    exit_code: int | None
    end_reason: str | None = None
    output_tail: str = ""

    @property
    def stderr_tail(self) -> str:
        return self.output_tail


class _SlotReleasingExecutor:
    """Wraps a `ToolExecutor` so the CLI slot is released while a tool executes (preventing parent/subagent deadlock)."""

    def __init__(self, inner: ToolExecutor) -> None:
        self._inner = inner
        self.handle: NativeTurnHandle | None = None

    def list_tools(self) -> Sequence[McpToolSpec]:
        return self._inner.list_tools()

    def is_known_tool(self, name: str) -> bool:
        fn = getattr(self._inner, "is_known_tool", None)
        if callable(fn):
            return bool(fn(name))
        return any(s.name == name for s in self._inner.list_tools())

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        handle = self.handle
        if handle is not None:
            handle._before_tool_call()
        try:
            return self._inner.call_tool(request)
        finally:
            if handle is not None:
                handle._after_tool_call()


class NativeTurnHandle:
    """Manages one active CLI subprocess and its loopback MCP server for a turn."""

    def __init__(
        self,
        *,
        proc: Any,
        cmd: list[str],
        requested_model: str,
        server_name: str,
        bg_server: BackgroundTurnToolServer,
        temp_files: Sequence[Path],
        slots: Any,
        cancel_check: Callable[[], bool],
        deadline_monotonic: float,
        clock: Callable[[], float],
        kill_tree_fn: Callable[[Any], None],
        log_run_fn: Callable[..., None],
        stdout_queue: queue.Queue[str | None],
        stderr_chunks: list[str],
        stdout_thread: threading.Thread,
        stderr_thread: threading.Thread,
        t0: float,
        adapter: CliAgentAdapter | None = None,
        ticket: CliSlotTicket | None = None,
        conversation_id: str = "",
        on_waiting_slot: Callable[..., None] | None = None,
    ) -> None:
        self._adapter: CliAgentAdapter = (
            adapter if adapter is not None else ClaudeCliAdapter()
        )
        self._proc = proc
        self._cmd = cmd
        self._requested_model = requested_model
        self._server_name = server_name
        self._bg_server = bg_server
        self._temp_files = list(temp_files)
        self._slots = slots
        self._ticket: CliSlotTicket | None = ticket
        self._conversation_id = str(conversation_id or "")
        self._on_waiting_slot = on_waiting_slot
        self._cancel_check = cancel_check
        self._deadline_monotonic = deadline_monotonic
        self._clock = clock
        self._kill_tree_fn = kill_tree_fn
        self._log_run_fn = log_run_fn
        self._stdout_queue = stdout_queue
        self._stderr_chunks = stderr_chunks
        self._stdout_thread = stdout_thread
        self._stderr_thread = stderr_thread
        self._t0 = t0

        self._parser: CliStreamTranslator = self._adapter.stream_translator()
        self._tool_bytes: dict[str, int] = {}
        self._tool_names: dict[str, str] = {}
        self._had_visible_output = False
        self._exit_code: int | None = None
        self._end_reason: str | None = None
        self._non_json_stdout_lines: list[str] = []

        self._slot_lock = threading.Lock()
        self._slot_cond = threading.Condition(self._slot_lock)
        self._reacquiring_slot = False
        self._active_tool_calls = 0
        self._holds_slot = True
        self._closed = False
        self._close_lock = threading.Lock()

    @property
    def mcp_url(self) -> str:
        return self._bg_server.handle.url

    @property
    def in_flight(self) -> int:
        with self._slot_lock:
            active = self._active_tool_calls + (1 if self._reacquiring_slot else 0)
        return max(active, self._bg_server.handle.in_flight)

    def is_process_alive(self) -> bool:
        return self._proc.poll() is None

    def _before_tool_call(self) -> None:
        with self._slot_cond:
            self._active_tool_calls += 1
            if self._holds_slot:
                if self._ticket is not None:
                    self._ticket.release()
                    self._ticket = None
                else:
                    self._slots.release()
                self._holds_slot = False
            self._slot_cond.notify_all()

    def _after_tool_call(self) -> None:
        with self._slot_cond:
            self._active_tool_calls = max(0, self._active_tool_calls - 1)
            while (
                self._active_tool_calls == 0
                and not self._closed
                and not self._holds_slot
            ):
                if self._reacquiring_slot:
                    self._slot_cond.wait(timeout=0.05)
                    continue
                self._reacquiring_slot = True
                self._slot_cond.release()
                new_ticket: CliSlotTicket | None = None
                try:
                    new_ticket = acquire_cli_slot(
                        self._conversation_id,
                        cancel_check=lambda: (
                            self._closed
                            or self._active_tool_calls > 0
                            or self._holds_slot
                            or self._cancel_check()
                            or (
                                self._deadline_monotonic is not None
                                and self._clock() >= self._deadline_monotonic
                            )
                        ),
                        on_wait=self._on_waiting_slot,
                        queue=self._slots,
                    )
                finally:
                    self._slot_cond.acquire()
                    self._reacquiring_slot = False
                    self._slot_cond.notify_all()
                if new_ticket is not None:
                    if (
                        self._closed
                        or self._active_tool_calls > 0
                        or self._holds_slot
                    ):
                        new_ticket.release()
                    else:
                        self._ticket = new_ticket
                        self._holds_slot = True
                else:
                    if (
                        self._closed
                        or self._cancel_check()
                        or (
                            self._deadline_monotonic is not None
                            and self._clock() >= self._deadline_monotonic
                        )
                    ):
                        break

    def refuse_tools(self, reason: str) -> None:
        self._bg_server.handle.refuse_calls(reason)

    def send_user(self, content: list[dict[str, Any]]) -> None:
        if not (PERSISTENT_STDIN_OK and self._adapter.capabilities.persistent_stdin):
            raise NativeLoopUnavailable("one-shot", status_code=503, exhausted=False)
        with self._close_lock:
            if self._closed or self._proc.poll() is not None:
                raise RuntimeError("Claude CLI process is already closed")
            stdin = getattr(self._proc, "stdin", None)
            if stdin is None or getattr(stdin, "closed", False):
                raise RuntimeError("Claude CLI stdin is closed")
            self._bg_server.handle.allow_calls()
            encode_fn = getattr(self._adapter, "encode_user_turn", None)
            if callable(encode_fn):
                line = encode_fn(content).decode("utf-8")
            else:
                line = user_event_line(content)
            stdin.write(line)
            stdin.flush()

    def _dispatch_event(
        self,
        ev: CliEvent,
        sink: NativeTurnSink,
        text_parts: list[str],
    ) -> NativeTurnOutcome | None:
        bad_kind = find_forbidden_item_kind(
            self._adapter.capabilities.forbidden_item_kinds,
            raw=ev.raw if isinstance(ev.raw, Mapping) else None,
            tool_name=ev.tool_name,
        )
        if bad_kind is not None:
            self.refuse_tools(f"forbidden item: {bad_kind}")
            self.close()
            raise CliSecurityAbort(
                f"CLI emitted forbidden item kind '{bad_kind}' (native tools must remain disabled)."
            )

        if ev.kind == CliEventKind.INIT:
            init = self._parser.init
            if init is not None:
                if init.api_key_source.lower() != "none":
                    self.close()
                    raise CliSecurityAbort(
                        f"Claude CLI reported apiKeySource='{init.api_key_source}' instead of 'none'. "
                        "Refusing to consume API credits."
                    )
                for tool_item in init.tools:
                    if (
                        tool_item
                        in self._adapter.capabilities.forbidden_item_kinds
                        or not (
                            tool_item.startswith(f"{self._server_name}/")
                            or tool_item.startswith(f"mcp__{self._server_name}__")
                            or tool_item.startswith(f"mcp_{self._server_name}_")
                        )
                    ):
                        self.refuse_tools(f"unauthorized init tool: {tool_item}")
                        self.close()
                        raise CliSecurityAbort(
                            f"CLI reported unauthorized tool '{tool_item}' in init.tools (native tools must remain disabled)."
                        )
                mcp_status = init.mcp_servers.get(self._server_name)
                if (
                    mcp_status != "connected"
                    and (
                        self._adapter.capabilities.reports_tool_inventory
                        or self._server_name in init.mcp_servers
                    )
                ):
                    self.close()
                    raise NativeLoopUnavailable(
                        f"Claude CLI MCP server '{self._server_name}' status is '{mcp_status}' (expected 'connected').",
                        status_code=503,
                        exhausted=False,
                    )
                sink.on_init(init)
            return None

        if ev.kind == CliEventKind.ERROR:
            self._end_reason = "stream_error"
            self.refuse_tools("stream error")
            err_msg = ev.text or "Claude CLI stream error"
            self.close()
            tail = self._build_output_tail()
            if not self._had_visible_output and self._parser.init is None:
                raise NativeLoopUnavailable(
                    err_msg,
                    status_code=503,
                    exhausted=False,
                    end_reason="stream_error",
                    output_tail=tail,
                    exit_code=self._exit_code,
                )
            raise ProviderTransportError(
                err_msg,
                status_code=502,
                exhausted=False,
                end_reason="stream_error",
                output_tail=tail,
                exit_code=self._exit_code,
            )

        if self._parser.init is None:
            if ev.kind == CliEventKind.RATE_LIMIT:
                if self._parser.rate_limit is not None:
                    sink.on_rate_limit(self._parser.rate_limit)
                return None
            res_before_init = ev.result if ev.result is not None else self._parser.result
            if (
                ev.kind == CliEventKind.RESULT
                and res_before_init is not None
                and res_before_init.is_error
                and res_before_init.subtype != "error_max_turns"
            ):
                self._end_reason = "error_result"
                self.refuse_tools("turn round error before init")
                err_info = self._adapter.classify_error(
                    res_before_init, self._parser.rate_limit
                )
                err = error_info_to_transport_error(err_info)
                self.close()
                tail = self._build_output_tail()
                err.end_reason = "error_result"
                err.output_tail = tail
                err.stderr_tail = tail
                err.exit_code = self._exit_code
                raise err
            self._end_reason = "stream_error"
            self.refuse_tools("missing init")
            self.close()
            tail = self._build_output_tail()
            raise NativeLoopUnavailable(
                f"Claude CLI emitted {ev.kind.value!r} before system/init validation.",
                status_code=503,
                exhausted=False,
                end_reason="stream_error",
                output_tail=tail,
                exit_code=self._exit_code,
            )

        if ev.message_id and hasattr(sink, "on_message_id"):
            try:
                sink.on_message_id(ev.message_id)  # type: ignore[attr-defined]
            except Exception:
                pass

        if ev.kind == CliEventKind.TEXT_DELTA:
            if ev.text:
                self._had_visible_output = True
                text_parts.append(ev.text)
                sink.on_text(ev.text)
            return None

        if ev.kind == CliEventKind.THINKING_DELTA:
            sink.on_thinking(ev.text)
            return None

        if ev.kind == CliEventKind.TOOL_USE_START:
            self._had_visible_output = True
            uid = ev.tool_use_id or ""
            tname = ev.tool_name or ""
            self._tool_bytes[uid] = 0
            self._tool_names[uid] = tname
            try:
                sink.on_tool_use_started(uid, tname, ev.message_id)  # type: ignore[call-arg]
            except TypeError:
                sink.on_tool_use_started(uid, tname)
            return None

        if ev.kind == CliEventKind.TOOL_INPUT_DELTA:
            uid = ev.tool_use_id or ""
            tname = ev.tool_name or self._tool_names.get(uid, "")
            added = len((ev.text or "").encode("utf-8"))
            self._tool_bytes[uid] = self._tool_bytes.get(uid, 0) + added
            sink.on_tool_input_progress(uid, tname, self._tool_bytes[uid])
            return None

        if ev.kind == CliEventKind.TOOL_USE_END:
            uid = ev.tool_use_id or ""
            tname = ev.tool_name or self._tool_names.get(uid, "")
            try:
                sink.on_tool_use_complete(uid, tname, ev.tool_input or {}, ev.message_id)  # type: ignore[call-arg]
            except TypeError:
                sink.on_tool_use_complete(uid, tname, ev.tool_input or {})
            return None

        if ev.kind == CliEventKind.USAGE:
            if ev.usage is not None:
                sink.on_assistant_usage(ev.message_id or "", ev.usage)
            return None

        if ev.kind == CliEventKind.COMPACT_BOUNDARY:
            sink.on_compacted()
            return None

        if ev.kind == CliEventKind.RATE_LIMIT:
            if self._parser.rate_limit is not None:
                sink.on_rate_limit(self._parser.rate_limit)
            return None

        if ev.kind == CliEventKind.RESULT:
            res = ev.result if ev.result is not None else self._parser.result
            if res is not None:
                self.refuse_tools("turn round completed")
                if res.is_error and res.subtype != "error_max_turns":
                    self._end_reason = "error_result"
                    err_info = self._adapter.classify_error(
                        res, self._parser.rate_limit
                    )
                    err = error_info_to_transport_error(err_info)
                    self.close()
                    tail = self._build_output_tail()
                    err.end_reason = "error_result"
                    err.output_tail = tail
                    err.stderr_tail = tail
                    err.exit_code = self._exit_code
                    raise err
                if res.is_error:
                    self._end_reason = "error_result"
                final_text = "".join(text_parts) or res.result_text
                return NativeTurnOutcome(
                    result=res,
                    init=self._parser.init,
                    final_text=final_text,
                    exit_code=self._proc.poll(),
                    end_reason=self._end_reason,
                    output_tail=self._build_output_tail(),
                )
        return None

    def _record_non_json_line(self, raw_line: str) -> None:
        stripped = raw_line.strip()
        if not stripped:
            return
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return
            except Exception:
                pass
        self._non_json_stdout_lines.append(raw_line.rstrip("\r\n"))
        if len(self._non_json_stdout_lines) > OUTPUT_TAIL_MAX_LINES * 2:
            self._non_json_stdout_lines = self._non_json_stdout_lines[
                -OUTPUT_TAIL_MAX_LINES:
            ]

    def _build_output_tail(self) -> str:
        combined_lines: list[str] = []
        if self._non_json_stdout_lines:
            combined_lines.extend(self._non_json_stdout_lines)
        stderr_text = "".join(self._stderr_chunks)
        if stderr_text:
            combined_lines.extend(stderr_text.splitlines())
        tail_lines = combined_lines[-OUTPUT_TAIL_MAX_LINES:]
        tail = "\n".join(tail_lines).strip()
        if len(tail) > OUTPUT_TAIL_MAX_CHARS:
            tail = tail[-OUTPUT_TAIL_MAX_CHARS:]
        return tail

    def _effective_idle_timeout_seconds(self) -> float:
        caps = getattr(self._adapter, "capabilities", None)
        cap_idle = getattr(caps, "idle_timeout_seconds", None)
        if (
            isinstance(cap_idle, (int, float))
            and not isinstance(cap_idle, bool)
            and cap_idle > 0
        ):
            if (
                IDLE_TIMEOUT_SECONDS != _DEFAULT_IDLE_TIMEOUT_SECONDS
                and getattr(self._adapter, "name", "claude") == "claude"
                and cap_idle == _DEFAULT_IDLE_TIMEOUT_SECONDS
            ):
                return float(IDLE_TIMEOUT_SECONDS)
            return float(cap_idle)
        return float(IDLE_TIMEOUT_SECONDS)

    @staticmethod
    def _emit_waiting_model(
        sink: NativeTurnSink, *, seconds: int, extended: bool
    ) -> None:
        fn = getattr(sink, "on_waiting_model", None)
        if not callable(fn):
            return
        try:
            fn(seconds, extended=extended)
        except TypeError:
            fn(seconds)

    def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
        reset_fn = getattr(self._parser, "reset_result", None)
        if callable(reset_fn):
            reset_fn()
        else:
            try:
                self._parser.result = None  # type: ignore[misc]
            except AttributeError:
                pass
        text_parts: list[str] = []
        idle_timeout = self._effective_idle_timeout_seconds()
        idle_started_at = self._clock()
        idle_deadline = idle_started_at + idle_timeout
        idle_extended = False

        while True:
            if self._cancel_check():
                self._end_reason = "cancelled"
                self.refuse_tools("turn cancelled")
                self.close()
                cancelled_res = CliResult(
                    subtype="cancelled",
                    is_error=False,
                    result_text="".join(text_parts),
                    stop_reason="cancelled",
                    num_turns=0,
                    duration_ms=int(max(0.0, self._clock() - self._t0) * 1000),
                    session_id=self._parser.init.session_id if self._parser.init else "",
                    api_error_status=None,
                    usage=self._parser.latest_usage(),
                    model_usage={},
                )
                return NativeTurnOutcome(
                    result=cancelled_res,
                    init=self._parser.init,
                    final_text="".join(text_parts),
                    exit_code=self._exit_code,
                    end_reason="cancelled",
                    output_tail=self._build_output_tail(),
                )

            has_queued_line = False
            line: str | None = None
            try:
                line = self._stdout_queue.get_nowait()
                has_queued_line = True
            except queue.Empty:
                pass

            if not has_queued_line:
                now = self._clock()
                if now >= self._deadline_monotonic:
                    self._end_reason = "deadline"
                    self.refuse_tools("turn deadline exceeded")
                    self.close()
                    tail = self._build_output_tail()
                    raise ProviderTransportError(
                        "TURN_DEADLINE_EXCEEDED: Claude CLI native turn exceeded deadline",
                        status_code=None,
                        exhausted=False,
                        end_reason="deadline",
                        output_tail=tail,
                        exit_code=self._exit_code,
                    )

                if self.in_flight > 0:
                    idle_started_at = now
                    idle_deadline = now + idle_timeout
                    idle_extended = False
                elif now >= idle_deadline:
                    if (
                        not idle_extended
                        and self._proc.poll() is None
                        and now < self._deadline_monotonic
                    ):
                        idle_extended = True
                        idle_deadline = now + (idle_timeout / 2.0)
                        elapsed_idle = max(
                            int(idle_timeout),
                            int(round(now - idle_started_at)),
                        )
                        self._emit_waiting_model(
                            sink, seconds=elapsed_idle, extended=True
                        )
                    else:
                        self._end_reason = "idle_timeout"
                        self.refuse_tools("idle timeout")
                        self.close()
                        tail = self._build_output_tail()
                        elapsed_idle = max(
                            int(idle_timeout),
                            int(round(now - idle_started_at)),
                        )
                        raise ProviderTransportError(
                            f"Claude CLI sem saída por {elapsed_idle}s",
                            status_code=None,
                            exhausted=False,
                            end_reason="idle_timeout",
                            output_tail=tail,
                            exit_code=self._exit_code,
                        )

                try:
                    line = self._stdout_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

            if line is None:
                for ev in self._parser.flush_pending():
                    outcome = self._dispatch_event(ev, sink, text_parts)
                    if outcome is not None:
                        return outcome
                break

            now_after_line = self._clock()
            idle_started_at = now_after_line
            idle_deadline = now_after_line + idle_timeout
            idle_extended = False
            self._record_non_json_line(line)
            bad_line_kind = find_forbidden_item_kind(
                self._adapter.capabilities.forbidden_item_kinds,
                raw_line=line,
            )
            if bad_line_kind is not None:
                self.refuse_tools(f"forbidden item: {bad_line_kind}")
                self.close()
                raise CliSecurityAbort(
                    f"CLI emitted forbidden item kind '{bad_line_kind}' (native tools must remain disabled)."
                )
            for ev in self._parser.feed_line(line):
                outcome = self._dispatch_event(ev, sink, text_parts)
                if outcome is not None:
                    return outcome

        if self._cancel_check():
            self._end_reason = "cancelled"
            self.close()
            cancelled_res = CliResult(
                subtype="cancelled",
                is_error=False,
                result_text="".join(text_parts),
                stop_reason="cancelled",
                num_turns=0,
                duration_ms=int(max(0.0, self._clock() - self._t0) * 1000),
                session_id=self._parser.init.session_id if self._parser.init else "",
                api_error_status=None,
                usage=self._parser.latest_usage(),
                model_usage={},
            )
            return NativeTurnOutcome(
                result=cancelled_res,
                init=self._parser.init,
                final_text="".join(text_parts),
                exit_code=self._exit_code,
                end_reason="cancelled",
                output_tail=self._build_output_tail(),
            )

        self._end_reason = "stdout_eof"
        try:
            self._exit_code = self._proc.wait(timeout=1.0)
        except Exception:
            self._exit_code = self._proc.poll()

        self.close()
        tail = self._build_output_tail()
        stderr_tail = "".join(self._stderr_chunks).strip() or tail
        if not self._had_visible_output and self._parser.init is None:
            raise NativeLoopUnavailable(
                f"Claude CLI exited before initialization (code={self._exit_code}): {stderr_tail}",
                status_code=503,
                exhausted=False,
                end_reason="stdout_eof",
                output_tail=tail,
                exit_code=self._exit_code,
            )
        raise ProviderTransportError(
            f"Claude CLI exited without result packet (code={self._exit_code}): {stderr_tail}",
            status_code=502,
            exhausted=False,
            end_reason="stdout_eof",
            output_tail=tail,
            exit_code=self._exit_code,
        )

    def close(self) -> None:
        with self._close_lock:
            with self._slot_cond:
                if self._closed:
                    return
                self._closed = True
                should_release_slot = self._holds_slot
                ticket_to_release = self._ticket
                self._ticket = None
                self._holds_slot = False
                self._slot_cond.notify_all()

            try:
                self._bg_server.handle.refuse_calls("turn closed")
            except Exception:
                pass

            try:
                stdin = getattr(self._proc, "stdin", None)
                if stdin is not None and not getattr(stdin, "closed", False):
                    stdin.close()
            except Exception:
                pass

            try:
                if self._proc.poll() is None:
                    try:
                        self._exit_code = self._proc.wait(timeout=1.5 if not getattr(self._adapter.capabilities, "persistent_stdin", True) else 0.3)
                    except Exception:
                        self._kill_tree_fn(self._proc)
                        try:
                            self._exit_code = self._proc.wait(timeout=1.0)
                        except Exception:
                            self._exit_code = self._proc.poll()
                else:
                    self._exit_code = self._proc.poll()
                for s_attr in ("stdout", "stderr"):
                    s_obj = getattr(self._proc, s_attr, None)
                    if s_obj is not None:
                        try:
                            s_obj.close()
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                self._stdout_thread.join(timeout=0.5)
            except Exception:
                pass
            try:
                self._stderr_thread.join(timeout=0.5)
            except Exception:
                pass

            try:
                self._bg_server.close(timeout=2.0)
            except Exception:
                pass

            for tmp in self._temp_files:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

            if should_release_slot:
                if ticket_to_release is not None:
                    ticket_to_release.release()
                else:
                    self._slots.release()

            duration_s = max(0.0, self._clock() - self._t0)
            loggable_fn = getattr(self._adapter, "loggable_command", None)
            combined_tail = self._build_output_tail()
            try:
                self._log_run_fn(
                    mode="native",
                    argv=self._cmd,
                    requested_model=self._requested_model,
                    init=self._parser.init,
                    result=self._parser.result,
                    usage=self._parser.latest_usage(),
                    duration_s=duration_s,
                    exit_code=self._exit_code,
                    stderr_tail=combined_tail,
                    loggable_command=loggable_fn,
                    end_reason=self._end_reason,
                )
            except TypeError:
                try:
                    self._log_run_fn(
                        mode="native",
                        argv=self._cmd,
                        requested_model=self._requested_model,
                        init=self._parser.init,
                        result=self._parser.result,
                        usage=self._parser.latest_usage(),
                        duration_s=duration_s,
                        exit_code=self._exit_code,
                        stderr_tail=combined_tail,
                        loggable_command=loggable_fn,
                    )
                except TypeError:
                    self._log_run_fn(
                        mode="native",
                        argv=loggable_fn(self._cmd) if callable(loggable_fn) else self._cmd,
                        requested_model=self._requested_model,
                        init=self._parser.init,
                        result=self._parser.result,
                        usage=self._parser.latest_usage(),
                        duration_s=duration_s,
                        exit_code=self._exit_code,
                        stderr_tail=combined_tail,
                    )


def start_native_turn_for_provider(
    provider: Any,
    request: NativeTurnRequest,
) -> NativeTurnHandle:
    """Spawn a CLI native MCP turn for `ClaudeCliProvider`."""
    now_pre = provider._clock()
    if request.deadline_monotonic <= now_pre:
        raise ProviderTransportError(
            "TURN_DEADLINE_EXCEEDED: Claude CLI native turn deadline already expired",
            status_code=None,
            exhausted=False,
        )

    resolved_binary = provider._resolve_binary()
    if not resolved_binary:
        raise NativeLoopUnavailable(
            "Claude Code CLI executable ('claude') was not found on this machine.",
            status_code=503,
            exhausted=False,
        )

    adapter: CliAgentAdapter = getattr(provider, "adapter", None) or ClaudeCliAdapter()
    effective_base_env = merge_with_system_env(provider._env)

    if provider._popen_factory is None:
        if getattr(adapter, "name", "claude") == "claude":
            ver: Any = claude_cli_version(resolved_binary, env=effective_base_env)
        else:
            _, ver = adapter.resolve_binary(effective_base_env)
        if ver is not None and not adapter.version_supported(ver):
            raise NativeLoopUnavailable(
                f"Claude CLI version {ver} is outside validated range {VALIDATED_CLI_RANGE}",
                status_code=503,
                exhausted=False,
            )

    slots = provider._effective_slots()

    def _slot_cancel_check() -> bool:
        if request.cancel_check is not None and request.cancel_check():
            return True
        if request.deadline_monotonic is not None and provider._clock() >= request.deadline_monotonic:
            return True
        return False

    ticket = acquire_cli_slot(
        request.conversation_id,
        cancel_check=_slot_cancel_check,
        on_wait=request.on_waiting_slot,
        queue=slots,
    )
    if ticket is None:
        if (
            request.deadline_monotonic is not None
            and provider._clock() >= request.deadline_monotonic
            and not (request.cancel_check is not None and request.cancel_check())
        ):
            raise ProviderTransportError(
                "TURN_DEADLINE_EXCEEDED: Turn deadline exceeded before CLI slot was acquired",
                status_code=None,
                exhausted=False,
            )
        raise NativeLoopUnavailable(
            "Turn cancelled while waiting for a Claude CLI process slot.",
            status_code=499,
            exhausted=False,
        )

    if request.deadline_monotonic is not None and provider._clock() >= request.deadline_monotonic:
        ticket.release()
        raise ProviderTransportError(
            "TURN_DEADLINE_EXCEEDED: Turn deadline exceeded after acquiring CLI slot",
            status_code=None,
            exhausted=False,
        )

    wrapped_executor = _SlotReleasingExecutor(request.executor)
    # The adapter's cap is the client's default per-call limit; the real one is set below.
    cap_tool_s = getattr(
        getattr(adapter, "capabilities", None), "max_tool_call_seconds", None
    )
    cap_targets = [
        bind_cap
        for target_ts in (
            getattr(request.executor, "toolset", None),
            getattr(getattr(request.executor, "runtime", None), "toolset", None),
        )
        if callable(bind_cap := getattr(target_ts, "set_max_tool_call_seconds", None))
    ]
    for bind_cap in cap_targets:
        bind_cap(cap_tool_s)
    bg_server: BackgroundTurnToolServer | None = None
    temp_files: list[Path] = []
    proc: Any | None = None

    try:
        bg_server = BackgroundTurnToolServer.start(
            TurnToolServer(wrapped_executor, server_name=request.server_name)
        )
        ensure_isolated_cwd(request.cwd)
        wire_fn = getattr(adapter, "wire_tool_server", None)
        if callable(wire_fn):
            wiring_dir = (
                request.cwd
                if getattr(adapter, "name", "") == "antigravity"
                else Path(tempfile.gettempdir())
            )
            wiring = wire_fn(
                bg_server.handle,
                wiring_dir,
            )
        else:
            mcp_cfg = adapter.write_tool_server_config(
                bg_server.handle,
                Path(tempfile.gettempdir()),
            )
            wiring = ToolServerWiring(
                argv=("--mcp-config", str(mcp_cfg)),
                env={},
                files=(mcp_cfg,),
            )
        temp_files.extend(wiring.files)
        mcp_config_path = wiring.files[0] if wiring.files else None

        prompt_path = write_owner_only_temp(
            with_isolated_cli_note(request.system_prompt or "You are a helpful assistant.", request.server_name),
            prefix="orin-claude-sys-",
            suffix=".txt",
        )
        temp_files.append(prompt_path)

        now = provider._clock()
        remaining_s = max(1.0, request.deadline_monotonic - now)
        mcp_tool_timeout_ms = max(1_000, int(remaining_s * 1000))
        if cap_tool_s is not None:
            # build_command raises the client's per-call limit to this value (agy:
            # timeoutSeconds in mcp_config.json, checked against agy 1.5.4), so a
            # foreground command only needs to go to the background before it.
            for bind_cap in cap_targets:
                bind_cap(mcp_tool_timeout_ms / 1000)

        isolated_env = dict(
            adapter.environment(
                effective_base_env,
                max_output_tokens=request.max_output_tokens,
                tool_timeout_ms=mcp_tool_timeout_ms,
            )
        )
        if wiring.env:
            isolated_env.update(wiring.env)

        session_kwargs: dict[str, Any] = {}
        if request.session is not None and hasattr(request.session, "cli_flags"):
            flags = request.session.cli_flags()
            if isinstance(flags, Mapping):
                session_kwargs.update(flags)
        resume_session_id = (
            request.resume_session_id
            or session_kwargs.get("resume_session_id")
            or session_kwargs.get("resume")
            or (
                getattr(request.session, "session_id", None)
                if getattr(request.session, "resume", False)
                else None
            )
        )

        cmd = adapter.build_command(
            CliTurnRequest(
                binary=resolved_binary,
                system_prompt_file=prompt_path,
                model=request.model or provider.model,
                effort=request.effort,
                fallback_model=request.fallback_model,
                max_turns=max(1, int(request.max_turns)),
                tool_server_config_file=mcp_config_path,
                tool_server_name=request.server_name,
                tool_server_wiring=wiring,
                tool_timeout_ms=mcp_tool_timeout_ms,
                capabilities=adapter.capabilities,
                autocompact=request.autocompact,
                session_id=session_kwargs.get("session_id"),
                resume_session_id=resume_session_id,
                persist_session=bool(
                    session_kwargs.get(
                        "persist_session",
                        not getattr(adapter.capabilities, "persistent_stdin", True),
                    )
                ),
            )
        )

        popen_cmd = (
            [sys.executable, *cmd]
            if provider._popen_factory is None and str(cmd[0]).lower().endswith(".py")
            else cmd
        )
        popen_fn = provider._popen_factory or subprocess.Popen
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        encode_fn = getattr(adapter, "encode_user_turn", None)
        if callable(encode_fn):
            initial_payload = encode_fn(request.user_content).decode("utf-8")
        else:
            initial_payload = user_event_line(request.user_content)

        t0 = provider._clock()
        proc = popen_fn(
            popen_cmd,
            cwd=str(request.cwd),
            env=isolated_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )

        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stderr_chunks: list[str] = []

        def _read_stdout() -> None:
            try:
                if proc.stdout is not None:
                    for raw_line in iter(proc.stdout.readline, ""):
                        stdout_queue.put(raw_line)
            except Exception:
                pass
            finally:
                stdout_queue.put(None)

        def _read_stderr() -> None:
            try:
                if proc.stderr is not None:
                    data = proc.stderr.read()
                    if data:
                        stderr_chunks.append(data)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        if proc.stdin is not None:
            try:
                proc.stdin.write(initial_payload)
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            if not (PERSISTENT_STDIN_OK and adapter.capabilities.persistent_stdin):
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

        from agentos.providers.claude_cli.provider import kill_process_tree, log_cli_run

        handle = NativeTurnHandle(
            proc=proc,
            cmd=cmd,
            requested_model=request.model or provider.model,
            server_name=request.server_name,
            bg_server=bg_server,
            temp_files=temp_files,
            slots=slots,
            cancel_check=request.cancel_check,
            deadline_monotonic=request.deadline_monotonic,
            clock=provider._clock,
            kill_tree_fn=kill_process_tree,
            log_run_fn=log_cli_run,
            stdout_queue=stdout_queue,
            stderr_chunks=stderr_chunks,
            stdout_thread=stdout_thread,
            stderr_thread=stderr_thread,
            t0=t0,
            adapter=adapter,
            ticket=ticket,
            conversation_id=request.conversation_id,
            on_waiting_slot=request.on_waiting_slot,
        )
        wrapped_executor.handle = handle
        return handle

    except Exception as exc:
        if proc is not None:
            try:
                from agentos.providers.claude_cli.provider import kill_process_tree

                kill_process_tree(proc)
            except Exception:
                pass
        if bg_server is not None:
            try:
                bg_server.close(timeout=1.0)
            except Exception:
                pass
        for tmp in temp_files:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        ticket.release()
        # A security abort is never "native unavailable": that would fall back to the text path with the
        # same credentials.
        if isinstance(exc, (NativeLoopUnavailable, CliSecurityAbort)):
            raise
        raise NativeLoopUnavailable(
            f"Failed to start native Claude CLI turn: {exc}",
            status_code=503,
            exhausted=False,
        ) from exc
