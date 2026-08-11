from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from collections.abc import Mapping
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
        object.__setattr__(self, "payload", _freeze_payload(self.payload))


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


_MAX_PAYLOAD_DEPTH = 4
_MAX_PAYLOAD_ITEMS = 32
_MAX_PAYLOAD_TEXT = 256
_SENSITIVE_KEYS = {
    "api_key",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "credential",
    "cookie",
    "prompt",
    "raw_input",
    "raw_output",
    "stack_trace",
}


def _freeze_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    count = [0]

    def freeze(value: object, depth: int) -> object:
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ValueError("payload exceeds nesting limit")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > _MAX_PAYLOAD_TEXT:
                raise ValueError("payload text exceeds limit")
            return value
        if isinstance(value, Mapping):
            if len(value) > _MAX_PAYLOAD_ITEMS:
                raise ValueError("payload contains too many entries")
            result: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("payload keys must be non-blank strings")
                normalized = key.lower().replace("-", "_")
                if normalized in _SENSITIVE_KEYS or any(part in normalized for part in _SENSITIVE_KEYS):
                    raise ValueError("payload contains protected data")
                count[0] += 1
                if count[0] > _MAX_PAYLOAD_ITEMS:
                    raise ValueError("payload exceeds item limit")
                result[key] = freeze(item, depth + 1)
            return MappingProxyType(result)
        if isinstance(value, (tuple, list)):
            if len(value) > _MAX_PAYLOAD_ITEMS:
                raise ValueError("payload contains too many entries")
            count[0] += len(value)
            if count[0] > _MAX_PAYLOAD_ITEMS:
                raise ValueError("payload exceeds item limit")
            return tuple(freeze(item, depth + 1) for item in value)
        raise ValueError("payload contains unsupported data")

    frozen = freeze(payload, 0)
    assert isinstance(frozen, Mapping)
    return frozen
