from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .models import CorrelationId, EventId, ExecutionId, Ownership, Version


class DataClassification(StrEnum):
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class ExecutionEventType(StrEnum):
    EXECUTION_QUEUED = "ExecutionQueued"
    EXECUTION_STARTED = "ExecutionStarted"
    EXECUTION_WAITING_FOR_TOOL = "ExecutionWaitingForTool"
    EXECUTION_WAITING_FOR_USER = "ExecutionWaitingForUser"
    EXECUTION_PAUSED = "ExecutionPaused"
    EXECUTION_RESUMED = "ExecutionResumed"
    EXECUTION_FINISHED = "ExecutionFinished"
    EXECUTION_FAILED = "ExecutionFailed"
    EXECUTION_CANCELLED = "ExecutionCancelled"


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: EventId
    event_type: ExecutionEventType
    event_version: int
    occurred_at: datetime
    source: str
    correlation_id: CorrelationId
    causation_id: str | None
    sequence: int
    ownership: Ownership
    execution_id: ExecutionId
    classification: DataClassification
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.source.strip():
            raise ValueError("event_id and source must be non-blank")
        if self.event_version < 1 or self.sequence < 1:
            raise ValueError("event_version and sequence must be positive")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: str
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    command_id: str
    decision: str
    from_state: str
    to_state: str
    resulting_version: Version

    def __post_init__(self) -> None:
        for field_name in (
            "audit_id",
            "user_id",
            "agent_id",
            "execution_id",
            "correlation_id",
            "purpose",
            "command_id",
            "decision",
            "from_state",
            "to_state",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be non-blank")
        if self.workspace_id is not None and not self.workspace_id.strip():
            raise ValueError("workspace_id must be non-blank when supplied")
        if self.resulting_version < 1:
            raise ValueError("resulting_version must be positive")


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    event: EventEnvelope
    source_execution_id: ExecutionId
    expected_source_version: Version

    def __post_init__(self) -> None:
        if self.source_execution_id != self.event.execution_id:
            raise ValueError("outbox source must match event execution")
        if self.expected_source_version < 1:
            raise ValueError("expected_source_version must be positive")
