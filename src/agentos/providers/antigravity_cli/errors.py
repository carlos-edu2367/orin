"""Error classification for Google Antigravity CLI (`agy`)."""
from __future__ import annotations

import math
import re
import time

from agentos.providers.claude_cli.errors import ProviderTransportError
from agentos.providers.cli_adapter import (
    CliErrorInfo,
    CliErrorKind,
    CliResult,
    RateLimitState,
)

_AUTH_CODE_RE = re.compile(r"\b401\b")
_RATE_LIMIT_CODE_RE = re.compile(r"\b429\b")
_MODEL_NOT_FOUND_CODE_RE = re.compile(r"\b404\b")
_TRANSIENT_CODE_RE = re.compile(r"\b(?:502|503|504|529)\b")
_RETRY_DURATION_RE = re.compile(
    r"\bin\s+(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)

_MODEL_NOT_FOUND_TERMS: tuple[str, ...] = (
    "invalid model selection",
    "not recognized as a known model",
    "model not found",
    "not among the models available",
    "unknown model",
)

_STRONG_AUTH_TERMS: tuple[str, ...] = (
    "not logged in",
    "api key not valid",
    "api_key_invalid",
    "unauthenticated",
    "authentication",
    "unauthorized",
)

_AUTH_TERMS: tuple[str, ...] = (
    *_STRONG_AUTH_TERMS,
    "sign in",
    "login",
)

_RATE_LIMIT_TERMS: tuple[str, ...] = (
    "rate limit",
    "quota",
    "resource_exhausted",
    "too many requests",
)

_RATE_LIMIT_STATUSES: frozenset[str] = frozenset(
    {"rejected", "exhausted", "rate_limited"}
)

_CONTEXT_OVERFLOW_TERMS: tuple[str, ...] = (
    "context",
    "too long",
    "token limit",
    "max_tokens",
)

_TRANSIENT_TERMS: tuple[str, ...] = (
    "service unavailable",
    "deadline exceeded",
    "connection reset",
    "timed out",
    "print_timeout",
)

_LOGIN_HINT = "Abra o `agy` uma vez no terminal e faça login com sua conta Google"


def _extract_retry_after_seconds(
    text: str,
    rate_limit: RateLimitState | None,
    *,
    now: float | None = None,
) -> float | None:
    match = _RETRY_DURATION_RE.search(text)
    if match:
        try:
            amount = float(match.group(1))
        except ValueError:
            amount = 0.0
        unit = match.group(2).lower()
        if unit.startswith("h"):
            seconds = amount * 3600.0
        elif unit.startswith("m"):
            seconds = amount * 60.0
        else:
            seconds = amount
        if math.isfinite(seconds) and seconds > 0:
            return seconds

    if rate_limit is not None and rate_limit.resets_at is not None:
        try:
            resets_at = float(rate_limit.resets_at)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(resets_at) or resets_at <= 0:
            return None
        if resets_at > 1_000_000_000:
            now_ts = time.time() if now is None else float(now)
            if not math.isfinite(now_ts):
                now_ts = time.time()
            return max(1.0, resets_at - now_ts)
        return max(1.0, resets_at)

    return None


def classify_antigravity_error(
    result: CliResult,
    rate_limit: RateLimitState | None = None,
    *,
    now: float | None = None,
) -> CliErrorInfo:
    """Classify an Antigravity CLI error result into one of the 6 canonical `CliErrorKind` categories."""
    raw_text = (
        (result.result_text or "").strip()
        or "Antigravity CLI execution failed"
    )
    raw_lower = raw_text.lower()
    stop_lower = str(result.stop_reason or "").strip().lower()
    api_status = (
        result.api_error_status
        if isinstance(result.api_error_status, int)
        and not isinstance(result.api_error_status, bool)
        else None
    )

    is_model_not_found = (
        api_status == 404
        or bool(_MODEL_NOT_FOUND_CODE_RE.search(raw_lower))
        or any(term in raw_lower for term in _MODEL_NOT_FOUND_TERMS)
    )
    if is_model_not_found:
        return CliErrorInfo(
            kind=CliErrorKind.MODEL_NOT_FOUND,
            status_code=404,
            user_message=raw_text,
            retryable=False,
            retry_after_seconds=None,
            exception=ProviderTransportError(
                raw_text, status_code=404, exhausted=False
            ),
        )

    rl_triggered = (
        rate_limit is not None
        and isinstance(rate_limit.status, str)
        and rate_limit.status.strip().lower() in _RATE_LIMIT_STATUSES
    )
    has_rate_limit_signal = (
        rl_triggered
        or api_status == 429
        or bool(_RATE_LIMIT_CODE_RE.search(raw_lower))
        or any(term in raw_lower for term in _RATE_LIMIT_TERMS)
    )

    has_strong_auth_signal = (
        api_status in (401, 403)
        or bool(_AUTH_CODE_RE.search(raw_lower))
        or any(term in raw_lower for term in _STRONG_AUTH_TERMS)
    )
    is_auth = (
        not rl_triggered
        and api_status != 429
        and (
            has_strong_auth_signal
            or (
                not has_rate_limit_signal
                and any(term in raw_lower for term in _AUTH_TERMS)
            )
        )
    )
    if is_auth:
        user_msg = (
            raw_text
            if _LOGIN_HINT in raw_text
            else f"{raw_text} — {_LOGIN_HINT}"
        )
        return CliErrorInfo(
            kind=CliErrorKind.AUTH,
            status_code=401,
            user_message=user_msg,
            retryable=False,
            retry_after_seconds=None,
            exception=ProviderTransportError(
                user_msg, status_code=401, exhausted=True
            ),
        )

    if has_rate_limit_signal:
        retry_after = _extract_retry_after_seconds(
            raw_text, rate_limit, now=now
        )
        return CliErrorInfo(
            kind=CliErrorKind.RATE_LIMITED,
            status_code=429,
            user_message=raw_text,
            retryable=True,
            retry_after_seconds=retry_after,
            exception=ProviderTransportError(
                raw_text,
                status_code=429,
                exhausted=False,
                retry_after_seconds=retry_after,
            ),
        )

    is_context_overflow = any(
        term in raw_lower for term in _CONTEXT_OVERFLOW_TERMS
    )
    if is_context_overflow:
        return CliErrorInfo(
            kind=CliErrorKind.CONTEXT_OVERFLOW,
            status_code=400,
            user_message=raw_text,
            retryable=False,
            retry_after_seconds=None,
            exception=ProviderTransportError(
                raw_text, status_code=400, exhausted=False
            ),
        )

    is_transient = (
        (
            api_status is not None
            and (
                api_status in (502, 503, 504, 529)
                or 500 <= api_status <= 599
            )
        )
        or bool(_TRANSIENT_CODE_RE.search(raw_lower))
        or stop_lower == "print_timeout"
        or any(term in raw_lower for term in _TRANSIENT_TERMS)
    )
    if is_transient:
        status_code = api_status if api_status is not None else 503
        return CliErrorInfo(
            kind=CliErrorKind.TRANSIENT,
            status_code=status_code,
            user_message=raw_text,
            retryable=True,
            retry_after_seconds=None,
            exception=ProviderTransportError(
                raw_text, status_code=status_code, exhausted=False
            ),
        )

    return CliErrorInfo(
        kind=CliErrorKind.FATAL,
        status_code=502,
        user_message=raw_text,
        retryable=False,
        retry_after_seconds=None,
        exception=ProviderTransportError(
            raw_text, status_code=502, exhausted=False
        ),
    )


__all__ = [
    "classify_antigravity_error",
]
