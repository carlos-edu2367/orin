"""Durable recovery contracts for execution effects and checkpoints.

The records intentionally carry references and bounded operational metadata.
Adapters may keep private result material in authorized storage, but neither
the journal nor its callers may put it in an outbox event or an activity feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from .models import ExecutionId, UserId, WorkspaceId


class ExecutionEffectKind(StrEnum):
    PROVIDER = "PROVIDER"
    TOOL = "TOOL"


class ExecutionEffectState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PREPARED = "PREPARED"
    IN_FLIGHT = "IN_FLIGHT"
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


class ExecutionEffectRetryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    POLICY_DEPENDENT = "POLICY_DEPENDENT"


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ExecutionJournalScope:
    execution_id: ExecutionId
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        for name in ("execution_id", "user_id", "agent_id", "correlation_id"):
            _text(str(getattr(self, name)), name)
        if self.workspace_id is not None:
            _text(str(self.workspace_id), "workspace_id")


@dataclass(frozen=True, slots=True)
class ExecutionEffect:
    effect_id: str
    scope: ExecutionJournalScope
    kind: ExecutionEffectKind
    invocation_ref: str
    request_ref: str
    idempotency_key: str
    state: ExecutionEffectState
    retryability: ExecutionEffectRetryability
    attempt: int
    prepared_at: datetime
    version: int = 1
    result_ref: str | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    resolved_at: datetime | None = None
    reconciliation_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("effect_id", "invocation_ref", "request_ref", "idempotency_key"):
            _text(getattr(self, name), name)
        if self.attempt < 1 or self.version < 1:
            raise ValueError("attempt and version must be positive")
        _aware(self.prepared_at, "prepared_at")
        for name in ("started_at", "resolved_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    checkpoint_id: str
    scope: ExecutionJournalScope
    sequence: int
    execution_state_version: int
    context_manifest_ref: str
    next_decision: str
    is_safe: bool
    created_at: datetime
    pending_effect_id: str | None = None
    snapshot: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for name in ("checkpoint_id", "context_manifest_ref", "next_decision"):
            _text(getattr(self, name), name)
        if self.pending_effect_id is not None:
            _text(self.pending_effect_id, "pending_effect_id")
        if self.sequence < 1 or self.execution_state_version < 1:
            raise ValueError("checkpoint sequence and execution state version must be positive")
        _aware(self.created_at, "created_at")


class ExecutionJournal(Protocol):
    def prepare(self, effect: ExecutionEffect) -> ExecutionEffect: ...
    def mark_in_flight(self, effect_id: str, scope: ExecutionJournalScope, *, now: datetime) -> ExecutionEffect: ...
    def resolve(
        self,
        effect_id: str,
        scope: ExecutionJournalScope,
        *,
        state: ExecutionEffectState,
        now: datetime,
        result_ref: str | None = None,
        error_code: str | None = None,
        private_result: Mapping[str, object] | None = None,
    ) -> ExecutionEffect: ...
    def save_checkpoint(self, checkpoint: ExecutionCheckpoint) -> ExecutionCheckpoint: ...
    def latest_safe(self, scope: ExecutionJournalScope) -> ExecutionCheckpoint | None: ...
    def unresolved(self, scope: ExecutionJournalScope) -> tuple[ExecutionEffect, ...]: ...


__all__ = [
    "ExecutionCheckpoint",
    "ExecutionEffect",
    "ExecutionEffectKind",
    "ExecutionEffectRetryability",
    "ExecutionEffectState",
    "ExecutionJournal",
    "ExecutionJournalScope",
]
