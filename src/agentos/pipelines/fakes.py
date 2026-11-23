"""Test doubles for the engine ports (WS-C6). No test of the pipeline engine spends quota: it uses these.

Nothing here sleeps for synchronization: scripted children block on ``threading.Event`` objects and the clock is
virtual. Waits that must time out use short real timeouts (a few milliseconds) and, when asked, advance the
``FakeClock`` by the requested amount so backoff and stall tests run in milliseconds.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .events import PipelineEvent
from .ports import (
    AgentCall,
    AgentHandle,
    AgentProgress,
    AgentResult,
    CommandResult,
    QuotaAssessment,
    VerifierRequest,
    VerifierResult,
)

REAL_WAIT_CAP = 0.02  # seconds of real time a fake wait may block


class SimulatedCrash(BaseException):
    """Stands for the process dying: derived from ``BaseException`` so no ``except Exception`` swallows it."""


class FakeClock:
    """Virtual clock. ``wait`` never sleeps: it advances time by the timeout unless the event is already set."""

    def __init__(self, start: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._now = start or datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
        self._mono = 1000.0
        self.waits: list[float] = []
        self.on_wait: Callable[[int, float], None] | None = None

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def monotonic(self) -> float:
        with self._lock:
            return self._mono

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += timedelta(seconds=seconds)
            self._mono += seconds

    def wait(self, event: threading.Event, timeout: float) -> bool:
        with self._lock:
            self.waits.append(float(timeout))
            index = len(self.waits)
        if self.on_wait is not None:
            self.on_wait(index, timeout)
        if event.is_set():
            return True
        self.advance(max(0.0, timeout))
        return event.is_set()


@dataclass(slots=True)
class Reply:
    """What a scripted child ends with."""

    status: str = "succeeded"
    text: str = "ok"
    structured_output: Any = None
    error_code: str | None = None
    input_tokens: int = 10
    output_tokens: int = 5
    cost_usd: float | None = 0.01
    cost_known: bool = True
    worktree: Mapping[str, Any] | None = None

    def to_result(self) -> AgentResult:
        return AgentResult(
            status=self.status,  # type: ignore[arg-type]
            text=self.text,
            structured_output=self.structured_output,
            error_code=self.error_code,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            cost_known=self.cost_known and self.cost_usd is not None,
            worktree=self.worktree,
        )


class AgentControl:
    """Handed to callable behaviors: lets them observe cancellation and report progress."""

    def __init__(self, clock: FakeClock | None) -> None:
        self.cancelled = threading.Event()
        self._clock = clock
        self.progress = AgentProgress()

    def report(self, **fields: Any) -> None:
        self.progress = replace(self.progress, **fields)

    def hang(self) -> Reply:
        """Block until cancelled (real wait on the event) and end as a cancelled child."""
        self.cancelled.wait()
        return Reply(status="cancelled", text="", input_tokens=0, output_tokens=0, cost_usd=None)


Behavior = Reply | Callable[[AgentCall, AgentControl], Reply]


@dataclass(slots=True)
class _Entry:
    handle: AgentHandle
    call: AgentCall
    control: AgentControl
    done: threading.Event = field(default_factory=threading.Event)
    result: AgentResult | None = None
    crash: BaseException | None = None


class FakeLauncher:
    """Scriptable ``AgentLauncher``. ``script(step_id, *replies, index=None)`` queues one behavior per launch;
    the last one repeats when the queue runs dry. Unscripted steps answer a plain success."""

    live_metering = False

    def __init__(self, clock: FakeClock | None = None, *, advance_on_wait: bool = False) -> None:
        self._clock = clock
        self._advance_on_wait = advance_on_wait
        self._lock = threading.Lock()
        self._scripts: dict[tuple[str, int | None], deque[Behavior]] = defaultdict(deque)
        self._last: dict[tuple[str, int | None], Behavior] = {}
        self._entries: dict[str, _Entry] = {}
        self._counter = 0
        self.calls: list[AgentCall] = []
        self.cancelled: list[tuple[str, str]] = []
        self.discarded: list[str] = []
        self.running = 0
        self.max_running = 0
        self.launch_count_by_step: dict[str, int] = defaultdict(int)
        self.on_launch: Callable[[AgentCall], None] | None = None

    # --------------------------------------------------------------- scripting
    def script(self, step_id: str, *behaviors: Behavior, index: int | None = None) -> None:
        with self._lock:
            self._scripts[(step_id, index)].extend(behaviors)

    def _behavior_for(self, call: AgentCall) -> Behavior:
        position = None
        if call.item_key and call.item_key[:3].isdigit():
            position = int(call.item_key[:3])
        for key in ((call.step_id, position), (call.step_id, None)):
            queue = self._scripts.get(key)
            if queue:
                behavior = queue.popleft()
                self._last[key] = behavior
                return behavior
            if key in self._last:
                return self._last[key]
        return Reply()

    @property
    def launches(self) -> int:
        return len(self.calls)

    def entry_count(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------ ports
    def launch(self, call: AgentCall) -> AgentHandle:
        if self.on_launch is not None:
            self.on_launch(call)
        with self._lock:
            self._counter += 1
            handle = AgentHandle(f"agent-{self._counter}", call.step_id, call.item_key)
            behavior = self._behavior_for(call)
            control = AgentControl(self._clock)
            if self._clock is not None:
                control.progress = AgentProgress(last_activity_at=self._clock.now())
            entry = _Entry(handle, call, control)
            self._entries[handle.agent_id] = entry
            self.calls.append(call)
            self.launch_count_by_step[call.step_id] += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        thread = threading.Thread(target=self._run, args=(entry, behavior), daemon=True, name=f"fake-{handle.agent_id}")
        thread.start()
        return handle

    def _run(self, entry: _Entry, behavior: Behavior) -> None:
        try:
            reply = behavior(entry.call, entry.control) if callable(behavior) else behavior
            if entry.control.cancelled.is_set() and reply.status == "succeeded" and callable(behavior):
                reply = Reply(status="cancelled", text="", input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
            entry.result = reply.to_result()
        except BaseException as exc:  # noqa: BLE001 - a SimulatedCrash must surface at ``wait`` like a dead process
            entry.crash = exc
        finally:
            with self._lock:
                self.running -= 1
            entry.done.set()

    def wait(self, handle: AgentHandle, timeout: float) -> AgentResult | None:
        entry = self._entries[handle.agent_id]
        if entry.done.wait(min(max(timeout, 0.0), REAL_WAIT_CAP)):
            if entry.crash is not None:
                raise entry.crash
            return entry.result
        if self._advance_on_wait and self._clock is not None:
            self._clock.advance(timeout)
        return None

    def cancel(self, handle: AgentHandle, reason: str) -> None:
        with self._lock:
            self.cancelled.append((handle.agent_id, reason))
        entry = self._entries.get(handle.agent_id)
        if entry is not None:
            entry.control.cancelled.set()

    def progress(self, handle: AgentHandle) -> AgentProgress:
        return self._entries[handle.agent_id].control.progress

    def discard_worktree(self, handle: AgentHandle) -> None:
        self.discarded.append(handle.agent_id)

    # ----------------------------------------------------------------- checks
    def all_finished(self) -> bool:
        return all(entry.done.is_set() for entry in self._entries.values())

    def unfinished(self) -> list[str]:
        return [key for key, entry in self._entries.items() if not entry.done.is_set()]


CommandBehavior = CommandResult | Callable[[str, int, threading.Event], CommandResult]


class FakeGateRunner:
    """Scriptable ``GateRunner``. Records every command it is asked to run (the S2 spy reads ``commands``)."""

    def __init__(self, default: CommandResult | None = None) -> None:
        self._lock = threading.Lock()
        self._default = default or CommandResult(0, "ok")
        self._queue: deque[CommandBehavior] = deque()
        self.commands: list[str] = []
        self.timeouts: list[int] = []

    def script(self, *behaviors: CommandBehavior) -> None:
        self._queue.extend(behaviors)

    def run_command(self, command: str, timeout_seconds: int, cancel: threading.Event) -> CommandResult:
        with self._lock:
            self.commands.append(command)
            self.timeouts.append(timeout_seconds)
            behavior = self._queue.popleft() if self._queue else self._default
        return behavior(command, timeout_seconds, cancel) if callable(behavior) else behavior


class FakeVerifier:
    def __init__(self, *results: VerifierResult, default: VerifierResult | None = None) -> None:
        self._queue = deque(results)
        self._default = default or VerifierResult("pass", review_id="review-1", reviewer="fake/verifier")
        self._lock = threading.Lock()
        self.requests: list[VerifierRequest] = []

    def review(self, request: VerifierRequest, cancel: threading.Event) -> VerifierResult:
        with self._lock:
            self.requests.append(request)
            return self._queue.popleft() if self._queue else self._default

    @property
    def calls(self) -> int:
        return len(self.requests)


class FakeQuota:
    def __init__(self, assessment: QuotaAssessment | None = None) -> None:
        self.assessment = assessment or QuotaAssessment()
        self.asked: list[tuple[str, ...]] = []

    def assess(self, providers: Sequence[str]) -> QuotaAssessment:
        self.asked.append(tuple(providers))
        return self.assessment


class FakeChangeScope:
    def __init__(self, files: Sequence[str] = ()) -> None:
        self.files = tuple(files)

    def changed_files(self) -> tuple[str, ...]:
        return self.files


@dataclass(frozen=True, slots=True)
class Recorded:
    event: PipelineEvent
    agent_id: str | None


class RecordingSink:
    """Collects the events the engine emits (thread-safe) and hands them back by type."""

    def __init__(self) -> None:
        self._lock = threading.Condition()
        self.items: list[Recorded] = []

    def emit(self, event: PipelineEvent, *, agent_id: str | None = None, parent_agent_id: str | None = None) -> None:
        with self._lock:
            self.items.append(Recorded(event, agent_id))
            self._lock.notify_all()

    def wait_for(self, predicate: Callable[[list[PipelineEvent]], bool], timeout: float = 10.0) -> bool:
        """Block (on a condition, not a sleep) until ``predicate(events)`` holds; ``False`` on timeout."""
        with self._lock:
            return self._lock.wait_for(lambda: predicate([item.event for item in self.items]), timeout)

    @property
    def events(self) -> list[PipelineEvent]:
        with self._lock:
            return [item.event for item in self.items]

    def types(self) -> list[str]:
        return [event.type.value for event in self.events]

    def of(self, event_type: str) -> list[PipelineEvent]:
        return [event for event in self.events if event.type.value == event_type]


__all__ = [
    "AgentControl",
    "Behavior",
    "FakeChangeScope",
    "FakeClock",
    "FakeGateRunner",
    "FakeLauncher",
    "FakeQuota",
    "FakeVerifier",
    "REAL_WAIT_CAP",
    "Recorded",
    "RecordingSink",
    "Reply",
    "SimulatedCrash",
]
