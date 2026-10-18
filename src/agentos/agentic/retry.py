"""When to retry a provider call and how long to wait: exponential backoff with jitter, or the server's retry-after."""

from __future__ import annotations

import random as _random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

RETRY_JITTER = 0.25
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def _finite_non_negative(value: float) -> bool:
    return 0 <= value < float("inf")  # NaN fails both comparisons


def parse_retry_after(headers: Any) -> float | None:
    """Seconds to wait from ``retry-after-ms`` or ``retry-after`` (seconds or an HTTP date); None if absent or invalid."""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw_ms = getter("retry-after-ms")
    if raw_ms is not None:
        try:
            value = float(str(raw_ms).strip()) / 1000.0
            if _finite_non_negative(value):
                return value
        except (TypeError, ValueError):
            pass
    raw = getter("retry-after")
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        value = float(text)
        return value if _finite_non_negative(value) else None
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def status_code_of(error: BaseException) -> int | None:
    for candidate in _chain(error):
        for status in (getattr(candidate, "status_code", None), getattr(getattr(candidate, "response", None), "status_code", None)):
            if isinstance(status, int) and not isinstance(status, bool):
                return status
    return None


def retry_after_of(error: BaseException) -> float | None:
    for candidate in _chain(error):
        value = getattr(candidate, "retry_after_seconds", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and _finite_non_negative(float(value)):
            return float(value)
        parsed = parse_retry_after(getattr(getattr(candidate, "response", None), "headers", None))
        if parsed is not None:
            return parsed
    return None


def is_retryable(error: BaseException) -> bool:
    """Connection errors, timeouts and malformed streams are retryable; a client error (4xx) mostly is not."""
    status = status_code_of(error)
    return status is None or status in RETRYABLE_STATUS


def backoff_delay(
    attempt: int,
    *,
    base: float,
    maximum: float,
    retry_after: float | None = None,
    jitter: float = RETRY_JITTER,
    rand: Callable[[], float] = _random.random,
) -> float:
    if retry_after is not None:
        return max(0.0, float(retry_after))
    if base <= 0:
        return 0.0
    exponent = min(max(0, int(attempt) - 1), 30)
    delay = min(float(maximum), float(base) * (2 ** exponent))
    spread = delay * float(jitter) * (2.0 * rand() - 1.0)
    return max(0.0, min(float(maximum), delay + spread))


__all__ = ["RETRYABLE_STATUS", "backoff_delay", "is_retryable", "parse_retry_after", "retry_after_of", "status_code_of"]
