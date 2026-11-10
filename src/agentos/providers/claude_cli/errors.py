from __future__ import annotations

from datetime import datetime, tzinfo
import re
import time

from agentos.agentic.provider_stream import ProviderKeyRejected
from agentos.providers.cli_adapter import (
    CliErrorInfo,
    CliErrorKind,
    CliResult,
    RateLimitState,
)

_API_ERROR_STATUS_RE = re.compile(r"\bAPI Error:\s*(\d{3})\b")


class ProviderTransportError(ProviderKeyRejected):
    """Raised when `claude -p` fails auth, security, protocol, or execution."""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        exhausted: bool = False,
        end_reason: str | None = None,
        output_tail: str = "",
        exit_code: int | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
            exhausted=exhausted,
        )
        self.end_reason = end_reason
        self.output_tail = output_tail
        self.stderr_tail = output_tail
        self.exit_code = exit_code


class NativeLoopUnavailable(ProviderTransportError):
    """Raised before the first user-visible delta when native MCP loop cannot start."""


class CliSecurityAbort(ProviderTransportError):
    """The CLI would not run on the OAuth subscription (apiKeySource != "none", or no init first).

    Never retried, never a fallback to another path or model: the same credentials would be used again.
    Not a NativeLoopUnavailable on purpose, so the native runner does not fall back to the text path.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message, status_code=401, exhausted=True)


class CliBuiltinToolDetour(CliSecurityAbort):
    """The CLI called one of its own tools that changes nothing (agy: manage_task, list_dir...).

    The process was stopped exactly as for any built-in tool (a CliSecurityAbort to every caller that does not
    know better), but the native runner may resume the turn from Orin's history telling the model the tool is
    not available. `tool_name` is the built-in tool's name.
    """

    def __init__(self, tool_name: str) -> None:
        self.tool_name = str(tool_name or "")
        super().__init__(
            f"CLI called its built-in tool '{self.tool_name}' (native tools must remain disabled); "
            "it changes nothing, so the turn can resume."
        )


def format_reset_local(resets_at: float, *, tz: tzinfo | None = None) -> str:
    """Format an epoch reset timestamp in the local timezone (or `tz`) as `%d/%m às %H:%M`."""
    try:
        dt = (
            datetime.fromtimestamp(float(resets_at), tz=tz)
            if tz is not None
            else datetime.fromtimestamp(float(resets_at)).astimezone()
        )
        return dt.strftime("%d/%m às %H:%M")
    except (OSError, OverflowError, ValueError):
        return "horário indisponível"


def classify_result_error(
    result: CliResult,
    rate_limit: RateLimitState | None,
    *,
    now: float | None = None,
    tz: tzinfo | None = None,
) -> ProviderTransportError:
    """Classify a `result` error packet into a `ProviderTransportError` with accurate HTTP status and retry metadata."""
    status = result.api_error_status
    raw_text = (result.result_text or "").strip() or "Claude Code CLI execution failed"

    if status is None:
        match = _API_ERROR_STATUS_RE.search(raw_text)
        if match:
            status = int(match.group(1))

    subscription_rejected = (
        rate_limit is not None and rate_limit.status.lower() == "rejected"
    )
    is_rate_limited = (
        status == 429
        or subscription_rejected
        or (status is None and "rate limit" in raw_text.lower())
    )

    if is_rate_limited:
        retry_after_seconds: float | None = None
        message = raw_text
        if subscription_rejected and rate_limit is not None and rate_limit.resets_at is not None:
            now_ts = time.time() if now is None else float(now)
            if rate_limit.resets_at > 1_000_000_000:
                retry_after_seconds = max(1.0, rate_limit.resets_at - now_ts)
            else:
                retry_after_seconds = max(1.0, rate_limit.resets_at)
            rl_type = rate_limit.rate_limit_type or "assinatura"
            formatted = format_reset_local(rate_limit.resets_at, tz=tz)
            message = f"Limite de uso da assinatura ({rl_type}) atingido; libera em {formatted} ({raw_text})"
        return ProviderTransportError(
            message,
            status_code=429,
            exhausted=False,
            retry_after_seconds=retry_after_seconds,
            end_reason="error_result",
        )

    if status in (401, 403):
        return ProviderTransportError(
            raw_text,
            status_code=401,
            exhausted=True,
            end_reason="error_result",
        )

    if status == 404:
        return ProviderTransportError(
            raw_text,
            status_code=404,
            exhausted=False,
            end_reason="error_result",
        )

    if status == 529:
        return ProviderTransportError(
            raw_text,
            status_code=529,
            exhausted=False,
            end_reason="error_result",
        )

    if status == 400:
        return ProviderTransportError(
            raw_text,
            status_code=400,
            exhausted=False,
            end_reason="error_result",
        )

    effective_status = (
        status if isinstance(status, int) and status >= 400 else 500
    )
    return ProviderTransportError(
        raw_text,
        status_code=effective_status,
        exhausted=False,
        end_reason="error_result",
    )


def classify_result_error_info(
    result: CliResult,
    rate_limit: RateLimitState | None,
    *,
    now: float | None = None,
    tz: tzinfo | None = None,
) -> CliErrorInfo:
    transport_err = classify_result_error(result, rate_limit, now=now, tz=tz)
    raw_text = (result.result_text or "").strip()
    raw_lower = raw_text.lower()

    raw_status = result.api_error_status
    if raw_status is None:
        match = _API_ERROR_STATUS_RE.search(raw_text)
        if match:
            raw_status = int(match.group(1))

    subscription_rejected = (
        rate_limit is not None and rate_limit.status.lower() == "rejected"
    )
    is_rate_limited = (
        raw_status == 429
        or subscription_rejected
        or (raw_status is None and "rate limit" in raw_lower)
    )

    if raw_status in (401, 403):
        kind = CliErrorKind.AUTH
        retryable = False
    elif raw_status == 404:
        kind = CliErrorKind.MODEL_NOT_FOUND
        retryable = False
    elif is_rate_limited:
        kind = CliErrorKind.RATE_LIMITED
        retryable = False
    elif (
        "prompt is too long" in raw_lower
        or "context_length_exceeded" in raw_lower
        or "context length" in raw_lower
        or "too many tokens" in raw_lower
    ):
        kind = CliErrorKind.CONTEXT_OVERFLOW
        retryable = False
    elif (
        raw_status == 529
        or (raw_status is not None and 500 <= raw_status <= 599)
        or "overloaded" in raw_lower
    ):
        kind = CliErrorKind.TRANSIENT
        retryable = True
    else:
        kind = CliErrorKind.FATAL
        retryable = False

    retry_after_seconds = transport_err.retry_after_seconds
    if (
        retry_after_seconds is None
        and kind == CliErrorKind.RATE_LIMITED
        and rate_limit is not None
        and rate_limit.resets_at is not None
    ):
        now_ts = time.time() if now is None else float(now)
        if rate_limit.resets_at > 1_000_000_000:
            retry_after_seconds = max(1.0, rate_limit.resets_at - now_ts)
        else:
            retry_after_seconds = max(1.0, rate_limit.resets_at)

    status_out = (
        raw_status
        if raw_status is not None
        else (429 if is_rate_limited else transport_err.status_code)
    )

    return CliErrorInfo(
        kind=kind,
        status_code=status_out,
        user_message=str(transport_err),
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        exception=transport_err,
    )


def error_info_to_transport_error(info: CliErrorInfo) -> ProviderTransportError:
    if isinstance(info.exception, ProviderTransportError):
        if info.exception.end_reason is None:
            info.exception.end_reason = "error_result"
        return info.exception
    return ProviderTransportError(
        info.user_message,
        status_code=401 if info.kind == CliErrorKind.AUTH else info.status_code,
        retry_after_seconds=info.retry_after_seconds,
        exhausted=(info.kind == CliErrorKind.AUTH),
        end_reason="error_result",
    )


__all__ = [
    "CliErrorInfo",
    "CliErrorKind",
    "CliSecurityAbort",
    "NativeLoopUnavailable",
    "ProviderTransportError",
    "RateLimitState",
    "classify_result_error",
    "classify_result_error_info",
    "error_info_to_transport_error",
    "format_reset_local",
]
