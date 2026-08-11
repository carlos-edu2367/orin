"""Typed, durable schedule values. Runtime timers and claims are not authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from agentos.workers.models import WorkerPool


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")


def _utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ScheduleType(StrEnum):
    FUTURE_EXECUTION = "FUTURE_EXECUTION"
    SKILL_RECURRENCE = "SKILL_RECURRENCE"
    WATCHDOG = "WATCHDOG"
    MAINTENANCE = "MAINTENANCE"


class ScheduleState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class OccurrenceState(StrEnum):
    PLANNED = "PLANNED"
    CLAIMED = "CLAIMED"
    MATERIALIZED = "MATERIALIZED"
    DISPATCHED = "DISPATCHED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class TargetKind(StrEnum):
    TASK = "TASK"
    SKILL = "SKILL"
    WATCHDOG = "WATCHDOG"
    MAINTENANCE = "MAINTENANCE"


@dataclass(frozen=True, slots=True)
class ScheduleTarget:
    kind: TargetKind
    immutable_ref: str
    destination_pool: WorkerPool

    @classmethod
    def task(cls, value: str) -> "ScheduleTarget":
        return cls(TargetKind.TASK, value, WorkerPool.AGENT)

    @classmethod
    def maintenance(cls, value: str) -> "ScheduleTarget":
        return cls(TargetKind.MAINTENANCE, value, WorkerPool.MAINTENANCE)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", TargetKind(self.kind))
        object.__setattr__(self, "destination_pool", WorkerPool(self.destination_pool))
        _text(self.immutable_ref, "immutable_ref")
        expected = {TargetKind.TASK: WorkerPool.AGENT, TargetKind.SKILL: WorkerPool.AGENT, TargetKind.WATCHDOG: WorkerPool.SCHEDULER, TargetKind.MAINTENANCE: WorkerPool.MAINTENANCE}[self.kind]
        if self.destination_pool is not expected:
            raise ValueError("target kind does not map to destination pool")


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    fire_at: datetime | None = None
    interval: timedelta | None = None
    anchor_at: datetime | None = None

    @classmethod
    def at(cls, fire_at: datetime) -> "ScheduleRule":
        return cls(fire_at=fire_at)

    @classmethod
    def fixed_interval(cls, interval: timedelta, anchor_at: datetime) -> "ScheduleRule":
        return cls(interval=interval, anchor_at=anchor_at)

    def __post_init__(self) -> None:
        if (self.fire_at is None) == (self.interval is None):
            raise ValueError("rule must define exactly one temporal form")
        if self.fire_at is not None:
            _utc(self.fire_at, "fire_at")
        if self.interval is not None:
            if self.interval <= timedelta(0) or self.anchor_at is None:
                raise ValueError("interval rules require a positive interval and anchor")
            _utc(self.anchor_at, "anchor_at")


@dataclass(frozen=True, slots=True)
class MisfirePolicy:
    kind: str
    grace: timedelta = timedelta(0)
    max_occurrences: int = 1

    @classmethod
    def fire_once_now(cls) -> "MisfirePolicy": return cls("FIRE_ONCE_NOW")
    @classmethod
    def skip_missed(cls, grace: timedelta) -> "MisfirePolicy": return cls("SKIP_MISSED", grace)

    def __post_init__(self) -> None:
        if self.kind not in {"SKIP_MISSED", "FIRE_ONCE_NOW", "CATCH_UP_BOUNDED"} or self.grace < timedelta(0) or self.max_occurrences < 1:
            raise ValueError("invalid misfire policy")


@dataclass(frozen=True, slots=True)
class OverlapPolicy:
    kind: str
    max_active: int = 1

    @classmethod
    def forbid_overlap(cls) -> "OverlapPolicy": return cls("FORBID_OVERLAP")
    @classmethod
    def serialize(cls) -> "OverlapPolicy": return cls("SERIALIZE")

    def __post_init__(self) -> None:
        if self.kind not in {"FORBID_OVERLAP", "SERIALIZE", "ALLOW_BOUNDED"} or self.max_active < 1:
            raise ValueError("invalid overlap policy")


@dataclass(frozen=True, slots=True)
class Schedule:
    schedule_id: str
    user_id: str
    workspace_id: str | None
    agent_id: str
    schedule_type: ScheduleType
    target: ScheduleTarget
    rule: ScheduleRule
    timezone: str
    state: ScheduleState
    version: int
    starts_at: datetime
    next_fire_at: datetime | None
    misfire_policy: MisfirePolicy
    overlap_policy: OverlapPolicy
    ends_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("schedule_id", "user_id", "agent_id", "timezone"):
            _text(getattr(self, field), field)
        if self.workspace_id is not None: _text(self.workspace_id, "workspace_id")
        object.__setattr__(self, "schedule_type", ScheduleType(self.schedule_type))
        object.__setattr__(self, "state", ScheduleState(self.state))
        if self.version < 1: raise ValueError("version must be positive")
        _utc(self.starts_at, "starts_at")
        if self.next_fire_at is not None: _utc(self.next_fire_at, "next_fire_at")
        if self.ends_at is not None: _utc(self.ends_at, "ends_at")
        required = {ScheduleType.FUTURE_EXECUTION: TargetKind.TASK, ScheduleType.SKILL_RECURRENCE: TargetKind.SKILL, ScheduleType.WATCHDOG: TargetKind.WATCHDOG, ScheduleType.MAINTENANCE: TargetKind.MAINTENANCE}[self.schedule_type]
        if self.target.kind is not required: raise ValueError("schedule type does not map to target kind")

    def with_state(self, state: ScheduleState, *, next_fire_at: datetime | None = None) -> "Schedule":
        return replace(self, state=state, version=self.version + 1, next_fire_at=next_fire_at if next_fire_at is not None else self.next_fire_at)


@dataclass(frozen=True, slots=True)
class ScheduleClaim:
    claim_id: str
    schedule_id: str
    occurrence_id: str
    occurrence_version: int
    worker_id: str
    fencing_token: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduleOccurrence:
    occurrence_id: str
    schedule_id: str
    schedule_version: int
    logical_scheduled_at: datetime
    state: OccurrenceState
    version: int = 1
    fencing_token: int = 0
    execution_id: str | None = None
    dispatch_id: str | None = None
    claim_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("occurrence_id", "schedule_id"): _text(getattr(self, field), field)
        _utc(self.logical_scheduled_at, "logical_scheduled_at")
        object.__setattr__(self, "state", OccurrenceState(self.state))
        if self.version < 1 or self.fencing_token < 0: raise ValueError("invalid occurrence version or fence")

    def materialized(self, execution_id: str, fence: int) -> "ScheduleOccurrence":
        if self.state is OccurrenceState.MATERIALIZED and self.execution_id == execution_id: return self
        if self.state is not OccurrenceState.CLAIMED or self.fencing_token != fence: raise ValueError("claim fencing token is not current")
        _text(execution_id, "execution_id")
        return replace(self, state=OccurrenceState.MATERIALIZED, execution_id=execution_id, claim_id=None, version=self.version + 1)

    def dispatched(self, dispatch_id: str, fence: int) -> "ScheduleOccurrence":
        if self.state is OccurrenceState.DISPATCHED and self.dispatch_id == dispatch_id: return self
        if self.state is not OccurrenceState.MATERIALIZED or self.fencing_token != fence: raise ValueError("occurrence fencing token is not current")
        _text(dispatch_id, "dispatch_id")
        return replace(self, state=OccurrenceState.DISPATCHED, dispatch_id=dispatch_id, version=self.version + 1)

