"""Retry policy of a step: which causes are retried, how long to wait, and what is never retried (WS-C6)."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .model import RetryPolicy
from .ports import NON_RETRYABLE_CODES, AgentResult, Clock

CAUSE_FAILED = "failed"
CAUSE_STALLED = "stalled"
CAUSE_SCHEMA_INVALID = "schema_invalid"
CAUSE_GATE_FAILED = "gate_failed"
SCHEMA_INVALID_CODE = "SCHEMA_INVALID"


def backoff_delay(retry_number: int, base: float = 5.0, cap: float = 60.0) -> float:
    """``min(cap, base * 2**(n-1))`` for the n-th retry (n starts at 1): 5, 10, 20, 40, 60, 60 s."""
    return min(cap, base * (2 ** max(0, retry_number - 1)))


def effective_policy(step: RetryPolicy | None, default: RetryPolicy | None) -> RetryPolicy:
    return step or default or RetryPolicy(0, ())


def cause_of(result: AgentResult) -> str | None:
    """The retry cause of a failed agent result; ``None`` for outcomes that are never retried here."""
    if result.status == "stalled":
        return CAUSE_STALLED
    if result.status == "failed":
        return CAUSE_SCHEMA_INVALID if result.error_code == SCHEMA_INVALID_CODE else CAUSE_FAILED
    return None  # cancelled and budget_exceeded stop the step for good


def is_retryable(policy: RetryPolicy, cause: str | None, error_code: str | None, retries_used: int) -> bool:
    if cause is None or error_code in NON_RETRYABLE_CODES:
        return False
    return retries_used < policy.max and cause in policy.on


@dataclass(frozen=True, slots=True)
class WaitOutcome:
    interrupted: bool  # the stop event ended the wait early
    delay: float


def wait_before_retry(clock: Clock, stop: threading.Event, retry_number: int, *, base: float, cap: float) -> WaitOutcome:
    """Sleep the backoff on ``Clock.wait`` over the stop event, so a cancel wakes it at once."""
    delay = backoff_delay(retry_number, base, cap)
    return WaitOutcome(clock.wait(stop, delay), delay)


__all__ = [
    "CAUSE_FAILED",
    "CAUSE_GATE_FAILED",
    "CAUSE_SCHEMA_INVALID",
    "CAUSE_STALLED",
    "SCHEMA_INVALID_CODE",
    "WaitOutcome",
    "backoff_delay",
    "cause_of",
    "effective_policy",
    "is_retryable",
    "wait_before_retry",
]
