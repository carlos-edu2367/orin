"""Durable dispatch identities and ephemeral work-lease values (RFC 801)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")


def _utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class WorkerPool(StrEnum):
    AGENT = "AGENT"
    BROWSER = "BROWSER"
    MAINTENANCE = "MAINTENANCE"
    SCHEDULER = "SCHEDULER"


class WorkKind(StrEnum):
    AGENT_EXECUTION = "AGENT_EXECUTION"
    BROWSER_ACTION = "BROWSER_ACTION"
    MAINTENANCE_OPERATION = "MAINTENANCE_OPERATION"
    SCHEDULE_EVALUATION = "SCHEDULE_EVALUATION"
    WATCHDOG_CHECK = "WATCHDOG_CHECK"


_DESTINATIONS = {
    WorkKind.AGENT_EXECUTION: WorkerPool.AGENT,
    WorkKind.BROWSER_ACTION: WorkerPool.BROWSER,
    WorkKind.MAINTENANCE_OPERATION: WorkerPool.MAINTENANCE,
    WorkKind.SCHEDULE_EVALUATION: WorkerPool.SCHEDULER,
    WorkKind.WATCHDOG_CHECK: WorkerPool.SCHEDULER,
}


def destination_pool_for(work_kind: WorkKind) -> WorkerPool:
    return _DESTINATIONS[WorkKind(work_kind)]


class DispatchState(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    QUARANTINED = "QUARANTINED"


class DispatchAttemptState(StrEnum):
    ENQUEUED = "ENQUEUED"
    LEASED = "LEASED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True, slots=True)
class WorkerOperationContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        for field in ("user_id", "agent_id", "execution_id", "correlation_id", "purpose", "actor"):
            _text(getattr(self, field), field)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_item_id: str
    dispatch_id: str
    dispatch_attempt_id: str
    execution_id: str
    context: WorkerOperationContext
    pool: WorkerPool
    work_kind: WorkKind
    payload_ref: str
    expected_execution_version: int
    not_before: datetime
    expires_at: datetime
    attempt_number: int
    attempt_limit: int
    timeout: timedelta
    idempotency_key: str

    def __post_init__(self) -> None:
        for field in ("work_item_id", "dispatch_id", "dispatch_attempt_id", "execution_id", "payload_ref", "idempotency_key"):
            _text(getattr(self, field), field)
        object.__setattr__(self, "pool", WorkerPool(self.pool))
        object.__setattr__(self, "work_kind", WorkKind(self.work_kind))
        if self.pool is not destination_pool_for(self.work_kind):
            raise ValueError("work_kind does not map to the supplied pool")
        if self.expected_execution_version < 1 or self.attempt_number < 1 or self.attempt_limit < self.attempt_number:
            raise ValueError("work item versions and attempts must be positive and bounded")
        if self.timeout <= timedelta(0):
            raise ValueError("timeout must be positive")
        _utc(self.not_before, "not_before")
        _utc(self.expires_at, "expires_at")
        if self.expires_at <= self.not_before:
            raise ValueError("expires_at must be after not_before")


@dataclass(frozen=True, slots=True)
class DispatchAttempt:
    dispatch_attempt_id: str
    dispatch_id: str
    attempt_number: int
    state: DispatchAttemptState
    version: int
    lease_id: str | None = None
    worker_id: str | None = None
    fencing_token: int | None = None
    lease_expires_at: datetime | None = None
    reason_code: str | None = None

    @classmethod
    def enqueued(cls, attempt_id: str, dispatch_id: str, attempt_number: int) -> "DispatchAttempt":
        return cls(attempt_id, dispatch_id, attempt_number, DispatchAttemptState.ENQUEUED, 1)

    def __post_init__(self) -> None:
        for field in ("dispatch_attempt_id", "dispatch_id"):
            _text(getattr(self, field), field)
        object.__setattr__(self, "state", DispatchAttemptState(self.state))
        if self.attempt_number < 1 or self.version < 1:
            raise ValueError("attempt_number and version must be positive")
        leased = self.state is DispatchAttemptState.LEASED
        if leased != all(value is not None for value in (self.lease_id, self.worker_id, self.fencing_token, self.lease_expires_at)):
            raise ValueError("leased attempts require a complete lease")
        if self.fencing_token is not None and self.fencing_token < 1:
            raise ValueError("fencing_token must be positive")
        if self.lease_expires_at is not None:
            _utc(self.lease_expires_at, "lease_expires_at")

    def acquire(self, lease_id: str, *, worker_id: str, fence: int, expires_at: datetime) -> "DispatchAttempt":
        if self.state is not DispatchAttemptState.ENQUEUED:
            raise ValueError("only enqueued attempts can be leased")
        _text(lease_id, "lease_id")
        _text(worker_id, "worker_id")
        _utc(expires_at, "expires_at")
        if fence < 1:
            raise ValueError("fencing token must be positive")
        return replace(self, state=DispatchAttemptState.LEASED, version=self.version + 1, lease_id=lease_id, worker_id=worker_id, fencing_token=fence, lease_expires_at=expires_at)

    def acknowledge(self, lease_id: str, *, fence: int) -> "DispatchAttempt":
        self._validate_lease(lease_id, fence)
        return replace(self, state=DispatchAttemptState.ACKNOWLEDGED, version=self.version + 1, lease_id=None, worker_id=None, fencing_token=None, lease_expires_at=None)

    def release(self, lease_id: str, *, fence: int, reason_code: str = "LEASE_RELEASED") -> "DispatchAttempt":
        self._validate_lease(lease_id, fence)
        return replace(self, state=DispatchAttemptState.RELEASED, version=self.version + 1, lease_id=None, worker_id=None, fencing_token=None, lease_expires_at=None, reason_code=reason_code)

    def _validate_lease(self, lease_id: str, fence: int) -> None:
        if self.state is not DispatchAttemptState.LEASED or self.lease_id != lease_id:
            raise ValueError("lease is not current")
        if self.fencing_token != fence:
            raise ValueError("fencing token is not current")


__all__ = ["DispatchAttempt", "DispatchAttemptState", "DispatchState", "WorkerOperationContext", "WorkerPool", "WorkItem", "WorkKind", "destination_pool_for"]
