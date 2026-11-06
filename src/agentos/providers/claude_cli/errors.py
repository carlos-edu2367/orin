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


class NativeLoopUnavailable(ProviderTransportError):
    """Raised before the first user-visible delta when native MCP loop cannot start."""


class CliSecurityAbort(ProviderTransportError):
    """The CLI would not run on the OAuth subscription (apiKeySource != "none", or no init first).

    Never retried, never a fallback to another path or model: the same credentials would be used again.
    Not a NativeLoopUnavailable on purpose, so the native runner does not fall back to the text path.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message, status_code=401, exhausted=True)


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
        )

    if status in (401, 403):
        return ProviderTransportError(
            raw_text,
            status_code=401,
            exhausted=True,
        )

    if status == 404:
        return ProviderTransportError(
            raw_text,
            status_code=404,
            exhausted=False,
        )

    if status == 529:
        return ProviderTransportError(
            raw_text,
            status_code=529,
            exhausted=False,
        )

    if status == 400:
        return ProviderTransportError(
            raw_text,
            status_code=400,
            exhausted=False,
        )

    effective_status = (
        status if isinstance(status, int) and status >= 400 else 500
    )
    return ProviderTransportError(
        raw_text,
        status_code=effective_status,
        exhausted=False,
    )


def classify_result_error_info(
    result: CliResult,
    rate_limit: RateLimitState | None,
    *,
    now: float | None = None,
    tz: tzinfo | None = None,
) -> CliErrorInfo:
    transport_err = classify_result_error(result, rate_limit, now=now, tz=tz)
    status = transport_err.status_code
    raw_lower = (result.result_text or "").lower()

    if status in (401, 403):
        kind = CliErrorKind.AUTH
        retryable = False
    elif status == 404:
        kind = CliErrorKind.MODEL_NOT_FOUND
        retryable = False
    elif status == 429 or (rate_limit is not None and rate_limit.status.lower() == "rejected"):
        kind = CliErrorKind.RATE_LIMITED
        retryable = False
    elif status == 529 or (status is not None and 500 <= status <= 599):
        kind = CliErrorKind.TRANSIENT
        retryable = True
    elif (
        "prompt is too long" in raw_lower
        or "context_length_exceeded" in raw_lower
        or "too many tokens" in raw_lower
    ):
        kind = CliErrorKind.CONTEXT_OVERFLOW
        retryable = False
    else:
        kind = CliErrorKind.FATAL
        retryable = False

    return CliErrorInfo(
        kind=kind,
        status_code=status,
        user_message=str(transport_err),
        retryable=retryable,
        retry_after_seconds=transport_err.retry_after_seconds,
    )


def error_info_to_transport_error(info: CliErrorInfo) -> ProviderTransportError:
    return ProviderTransportError(
        info.user_message,
        status_code=info.status_code,
        retry_after_seconds=info.retry_after_seconds,
        exhausted=(info.kind == CliErrorKind.AUTH),
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
