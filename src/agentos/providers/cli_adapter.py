"""Generic contract for headless CLI agent adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentos.providers.models import Measurement, ProviderUsage


@dataclass(frozen=True, slots=True)
class StreamUsage(ProviderUsage):
    """Extended usage snapshot preserving prompt cache creation and read metrics."""

    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


def _int_field(mapping: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return 0


@dataclass(frozen=True, slots=True)
class CliUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int | None = None
    context_window_used_tokens: int | None = field(default=None, compare=False)

    @property
    def context_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def to_stream_usage(self) -> StreamUsage:
        total_in = self.context_tokens
        return StreamUsage(
            input_tokens=total_in,
            output_tokens=self.output_tokens,
            total_tokens=total_in + self.output_tokens,
            cached_input_tokens=self.cache_read_input_tokens,
            reasoning_tokens=self.reasoning_tokens,
            measurement=Measurement.CONFIRMED,
            cache_read_input_tokens=self.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
        )

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | None) -> CliUsage:
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            input_tokens=_int_field(raw, "input_tokens", "inputTokens"),
            output_tokens=_int_field(raw, "output_tokens", "outputTokens"),
            cache_read_input_tokens=_int_field(
                raw, "cache_read_input_tokens", "cacheReadInputTokens"
            ),
            cache_creation_input_tokens=_int_field(
                raw, "cache_creation_input_tokens", "cacheCreationInputTokens"
            ),
        )


@dataclass(frozen=True, slots=True)
class CliInit:
    session_id: str
    model: str
    api_key_source: str
    mcp_servers: Mapping[str, str] = field(default_factory=dict)
    tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CliResult:
    subtype: str
    is_error: bool
    result_text: str
    stop_reason: str | None
    num_turns: int
    duration_ms: int
    session_id: str
    api_error_status: int | None
    usage: CliUsage
    model_usage: Mapping[str, CliUsage] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RateLimitState:
    status: str
    rate_limit_type: str | None = None
    resets_at: float | None = None

    @classmethod
    def from_info(cls, info: Mapping[str, Any]) -> RateLimitState:
        status = str(info.get("status") or "").strip()
        rl_type = (
            str(info.get("rateLimitType") or info.get("rate_limit_type") or "").strip()
            or None
        )
        resets_raw = info.get("resetsAt")
        if resets_raw is None:
            resets_raw = info.get("resets_at")
        if resets_raw is None:
            windows = info.get("unifiedWindows")
            if isinstance(windows, Mapping):
                for win in windows.values():
                    if isinstance(win, Mapping) and win.get("resetsAt") is not None:
                        resets_raw = win.get("resetsAt")
                        break
        resets_at: float | None = None
        if (
            isinstance(resets_raw, (int, float))
            and not isinstance(resets_raw, bool)
            and math.isfinite(float(resets_raw))
            and resets_raw > 0
        ):
            val = float(resets_raw)
            if val > 10_000_000_000:
                val /= 1000.0
            resets_at = val
        return cls(status=status, rate_limit_type=rl_type, resets_at=resets_at)


class CliEventKind(StrEnum):
    INIT = "init"
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    THINKING_SIGNATURE = "thinking_signature"
    TOOL_USE_START = "tool_use_start"
    TOOL_INPUT_DELTA = "tool_input_delta"
    TOOL_USE_END = "tool_use_end"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"
    RATE_LIMIT = "rate_limit"
    COMPACT_BOUNDARY = "compact_boundary"
    RESULT = "result"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CliEvent:
    kind: CliEventKind
    text: str = ""
    index: int | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: Mapping[str, Any] | None = None
    input_bytes_delta: int = 0
    message_id: str | None = None
    usage: CliUsage | None = None
    init: CliInit | None = None
    result: CliResult | None = None
    rate_limit: RateLimitState | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class CliErrorKind(StrEnum):
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    MODEL_NOT_FOUND = "model_not_found"
    CONTEXT_OVERFLOW = "context_overflow"
    TRANSIENT = "transient"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class CliErrorInfo:
    kind: CliErrorKind
    status_code: int | None
    user_message: str
    retryable: bool = False
    retry_after_seconds: float | None = None
    exception: Exception | None = None

    def __post_init__(self) -> None:
        if self.exception is None:
            object.__setattr__(self, "exception", RuntimeError(self.user_message))

    @property
    def http_status(self) -> int | None:
        return self.status_code


@dataclass(frozen=True, slots=True)
class CliModelInfo:
    id: str
    display_name: str
    context_window: int
    supported_efforts: tuple[str, ...] = ()
    input_modalities: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ToolServerWiring:
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    files: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class CliCapabilities:
    native_tool_loop: bool = False
    tool_list_changed: bool = False
    images_in_tool_results: bool = False
    persistent_stdin: bool = False
    reports_tool_inventory: bool = True
    forbidden_item_kinds: frozenset[str] = frozenset()
    idle_timeout_seconds: int | None = None
    max_tool_call_seconds: int | None = None
    # Largest stdin user message the CLI reads whole (None: bounded only by the window).
    # A turn's history travels in that first message, so it must fit there.
    max_user_message_bytes: int | None = None
    # The CLI reads its tool-server wiring from its working folder: every process gets its own folder.
    per_process_cwd: bool = False
    # Built-in tools of the CLI that still stop the process (they must stay unused) but neither change
    # anything nor reach outside, so the turn resumes from Orin's history instead of failing.
    recoverable_builtin_tools: frozenset[str] = frozenset()
    # (built-in tool, Orin tools that do that job), named in the note the resumed turn reads.
    builtin_tool_alternatives: tuple[tuple[str, tuple[str, ...]], ...] = ()


AdapterCapabilities = CliCapabilities


@dataclass(frozen=True, slots=True)
class CliTurnRequest:
    binary: str
    system_prompt_file: Path
    model: str | None
    effort: str | None = None
    fallback_model: str | None = None
    max_turns: int = 1
    tool_server_config_file: Path | None = None
    tool_server_name: str = "orin"
    tool_server_wiring: ToolServerWiring | None = None
    wiring: ToolServerWiring | None = None
    autocompact: str | int | None = None
    session_id: str | None = None
    resume_session_id: str | None = None
    persist_session: bool = False
    # How long one call to Orin's tool server may take (the turn's remaining deadline). An adapter passes it on its
    # command line or through ``environment(tool_timeout_ms=...)``, whichever its CLI reads.
    tool_timeout_ms: int | None = None
    capabilities: CliCapabilities = field(default_factory=CliCapabilities)
    prompt_via_stdin_once: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prompt_via_stdin_once",
            not bool(self.capabilities.persistent_stdin),
        )
        if self.tool_server_wiring is None and self.wiring is not None:
            object.__setattr__(self, "tool_server_wiring", self.wiring)
        elif self.wiring is None and self.tool_server_wiring is not None:
            object.__setattr__(self, "wiring", self.tool_server_wiring)
        if (
            self.tool_server_config_file is None
            and self.tool_server_wiring is not None
            and self.tool_server_wiring.files
        ):
            object.__setattr__(
                self, "tool_server_config_file", self.tool_server_wiring.files[0]
            )


class CliStreamTranslator(Protocol):
    def feed_line(self, line: str) -> Sequence[CliEvent]: ...

    def flush_pending(self) -> Sequence[CliEvent]: ...

    @property
    def init(self) -> CliInit | None: ...

    @property
    def result(self) -> CliResult | None: ...

    @property
    def rate_limit(self) -> RateLimitState | None: ...

    def latest_usage(self) -> CliUsage | None: ...

    @property
    def stop_reason(self) -> str | None: ...


@runtime_checkable
class CliAgentAdapter(Protocol):
    name: str
    capabilities: CliCapabilities

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]: ...

    def version_supported(
        self, version: tuple[int, int, int] | None
    ) -> bool: ...

    def build_command(self, request: CliTurnRequest) -> list[str]: ...

    def wire_tool_server(
        self, handle: Any, directory: Path
    ) -> ToolServerWiring: ...

    def encode_user_turn(
        self, content: list[dict[str, Any]]
    ) -> bytes: ...

    def loggable_command(self, argv: Sequence[str]) -> list[str]: ...

    def environment(
        self,
        base: Mapping[str, str] | None,
        *,
        max_output_tokens: int | None,
        tool_timeout_ms: int | None,
    ) -> dict[str, str]: ...

    def stream_translator(self) -> CliStreamTranslator: ...

    def classify_error(
        self, result: CliResult, rate_limit: RateLimitState | None
    ) -> CliErrorInfo: ...

    def models(self) -> Sequence[CliModelInfo]: ...

    def context_window(self, model_id: str | None) -> int: ...


__all__ = [
    "AdapterCapabilities",
    "CliAgentAdapter",
    "CliCapabilities",
    "CliErrorInfo",
    "CliErrorKind",
    "CliEvent",
    "CliEventKind",
    "CliInit",
    "CliModelInfo",
    "CliResult",
    "CliStreamTranslator",
    "CliTurnRequest",
    "CliUsage",
    "RateLimitState",
    "StreamUsage",
    "ToolServerWiring",
]
