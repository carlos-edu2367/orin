"""Ports of the pipeline engine (WS-C6).

The engine is pure Python: it talks to the rest of Orin only through these protocols. The real adapters
(session, gate runner, verifier, quota, change scope) arrive in WS-C7; ``fakes.py`` has the test doubles.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from .events import PipelineEvent

CancelToken = threading.Event

AgentStatus = Literal["succeeded", "failed", "cancelled", "budget_exceeded", "stalled"]

# Error codes of a child that must never be retried: the configuration does not change by itself.
NON_RETRYABLE_CODES = frozenset(
    {
        "CHILD_PROVIDER_PINNED",
        "CHILD_PROVIDER_UNAVAILABLE",
        "CHILD_MODEL_NOT_FAVORITE",
        "CHILD_QUOTA_BLOCKED",
        "GATE_PERMISSION_DENIED",
        "GATE_INPUT_INVALID",
        "GATE_SHELL_UNSUPPORTED",
        "GATE_REF_UNTRUSTED",
    }
)


@dataclass(frozen=True, slots=True)
class AgentCall:
    """One request to run a child agent for a step (or one item of a ``map``)."""

    step_id: str
    item_key: str
    attempt: int
    agent: str
    prompt: str
    provider: str | None
    model: str | None
    effort: str | None
    isolation: str | None
    output_schema: Mapping[str, Any] | None
    budget: Mapping[str, float | int] | None  # public ``Agent(budget=...)`` shape: max_tokens, max_seconds, max_cost_usd
    pipeline_run_id: str
    replan_note: str | None = None
    loop_count: int = 0
    item: Any = None


@dataclass(frozen=True, slots=True)
class AgentHandle:
    agent_id: str
    step_id: str = ""
    item_key: str = ""


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: AgentStatus
    text: str = ""
    structured_output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    cost_known: bool = False
    worktree: Mapping[str, Any] | None = None  # K19: {merge_state, merge_detail, worktree_path}
    schema_errors: tuple[str, ...] = ()

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class AgentProgress:
    """K11: what the engine may read about a running child without waiting for it."""

    last_activity_at: datetime | None = None
    tool_calls: int = 0
    current_tool: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of one gate command. ``exit_code`` is ``None`` for timeout, cancellation or denial."""

    exit_code: int | None
    output_tail: str = ""
    denied: bool = False
    timed_out: bool = False
    cancelled: bool = False
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class VerifierRequest:
    """K17: what the verifier gate asks the verifier port."""

    policy: str | None
    explicit: tuple[str | None, str | None] | None  # (provider, model) when both are set in the YAML
    effort: str | None
    pipeline_run_id: str
    step_id: str
    scope_files: tuple[str, ...] = ()
    trigger_reason: str = "pipeline"
    extra_instructions: str | None = None


@dataclass(frozen=True, slots=True)
class VerifierResult:
    verdict: str  # pass | fail | inconclusive
    findings: tuple[Mapping[str, Any], ...] = ()  # K5 rows: id, severity, category, claim, evidence, suggested_fix
    review_id: str | None = None
    reviewer: str | None = None
    fallback_step: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class QuotaAssessment:
    """K22: providers that deserve a cost warning and providers that block a background step."""

    warn: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


class AgentLauncher(Protocol):
    """Starts child agents and watches them. ``launch`` never blocks.

    ``live_metering``: a launcher whose children already feed the run's ``UsageBudget`` while they work (the
    session adapter does, through ``record_usage``) sets it to ``True``; the engine then does not add the
    reported tokens a second time.
    """

    def launch(self, call: AgentCall) -> AgentHandle: ...

    def wait(self, handle: AgentHandle, timeout: float) -> AgentResult | None:
        """The result, or ``None`` when the child is still running after ``timeout`` seconds."""
        ...

    def cancel(self, handle: AgentHandle, reason: str) -> None: ...

    def progress(self, handle: AgentHandle) -> AgentProgress: ...

    def discard_worktree(self, handle: AgentHandle) -> None:
        """K19: drop the pending worktree of an interrupted child that is about to be redone."""
        ...


class GateRunner(Protocol):
    def run_command(self, command: str, timeout_seconds: int, cancel: CancelToken) -> CommandResult: ...


class VerifierPort(Protocol):
    def review(self, request: VerifierRequest, cancel: CancelToken) -> VerifierResult: ...


class ChangeScopePort(Protocol):
    def changed_files(self) -> tuple[str, ...]: ...


class QuotaPort(Protocol):
    def assess(self, providers: Sequence[str]) -> QuotaAssessment: ...


class EventSink(Protocol):
    def emit(self, event: PipelineEvent, *, agent_id: str | None = None, parent_agent_id: str | None = None) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def wait(self, event: threading.Event, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds or until ``event`` is set; ``True`` when the event is set."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def wait(self, event: threading.Event, timeout: float) -> bool:
        return event.wait(max(0.0, timeout))


@runtime_checkable
class JournalStore(Protocol):
    """The subset of ``PipelineStore`` the engine uses."""

    def get_run(self, run_id: str) -> Any: ...

    def set_run_state(self, run_id: str, state: str, *, stop_reason: str | None = None, turn_id: str | None = None) -> Any: ...

    def add_usage(self, run_id: str, *, tokens: int = 0, cost_usd: Any = None, seconds: int = 0) -> None: ...

    def start_step(
        self,
        *,
        run_id: str,
        step_id: str,
        input_hash: str,
        item_key: str = "",
        loop_count: int = 0,
        agent_id: str | None = None,
        state: str = "running",
    ) -> Any: ...

    def finish_step(
        self,
        run_id: str,
        step_id: str,
        item_key: str,
        attempt: int,
        *,
        state: str,
        output: Any = None,
        error: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Any = None,
        agent_id: str | None = None,
    ) -> Any: ...

    def find_reusable(self, run_id: str, step_id: str, item_key: str, loop_count: int) -> Any: ...

    def steps_of(self, run_id: str) -> list[Any]: ...

    def load_output(self, run_id: str, output: Any) -> Any: ...


@dataclass(slots=True)
class EngineOptions:
    """Knobs the driver may set; the defaults are the production values."""

    poll_seconds: float = 1.0
    monitor_seconds: float = 1.0
    max_agents: int = 200  # G23
    max_step_executions: int = 500  # G23
    backoff_base_seconds: float = 5.0
    backoff_cap_seconds: float = 60.0
    rerun_gates: bool = False
    budget_override: bool = False
    default_provider: str | None = None
    default_model: str | None = None
    default_effort: str | None = None
    agent_digests: Mapping[str, str] = field(default_factory=dict)
    shell_dialect: str = "posix"  # S6: "posix" quotes with shlex; "cmd" refuses quoted inputs
    permission_mode: str | None = None


__all__ = [
    "AgentCall",
    "AgentHandle",
    "AgentLauncher",
    "AgentProgress",
    "AgentResult",
    "AgentStatus",
    "CancelToken",
    "ChangeScopePort",
    "Clock",
    "CommandResult",
    "EngineOptions",
    "EventSink",
    "GateRunner",
    "JournalStore",
    "NON_RETRYABLE_CODES",
    "QuotaAssessment",
    "QuotaPort",
    "SystemClock",
    "VerifierPort",
    "VerifierRequest",
    "VerifierResult",
]
