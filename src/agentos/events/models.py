from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Mapping

from .security import clearance_allows, freeze_payload


class DataClassification(StrEnum):
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class DeliveryDisposition(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


class OrderingRequirement(StrEnum):
    NONE = "NONE"
    PER_EXECUTION = "PER_EXECUTION"


class ReplayPolicy(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


class ReplayStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class RetentionStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    EMPTY = "EMPTY"
    EXPIRED = "EXPIRED"


class FailureKind(StrEnum):
    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"


class CommitState(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


def _required(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PayloadReference:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "payload reference")

    def __repr__(self) -> str:
        return "PayloadReference(<opaque>)"


@dataclass(frozen=True, slots=True)
class EventContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str

    def __post_init__(self) -> None:
        for field in ("user_id", "agent_id", "execution_id", "correlation_id", "purpose"):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if len(self.purpose) > 128:
            raise ValueError("purpose is too long")

    def __repr__(self) -> str:
        return (
            "EventContext("
            f"user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, "
            f"agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, "
            f"correlation_id={self.correlation_id!r}, purpose=<bounded>)"
        )


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    event_version: int
    occurred_at: datetime
    source: str
    correlation_id: str
    causation_id: str | None
    sequence: int | None
    user_id: str
    workspace_id: str | None
    execution_id: str | None
    classification: DataClassification
    payload: Mapping[str, object]
    agent_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("event_id", "event_type", "source", "correlation_id", "user_id"):
            _required(getattr(self, field), field)
        if re.fullmatch(r"[A-Z][A-Za-z0-9]*", self.event_type) is None:
            raise ValueError("event_type must use canonical PascalCase")
        if self.causation_id is not None:
            _required(self.causation_id, "causation_id")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.agent_id is not None:
            _required(self.agent_id, "agent_id")
        if self.execution_id is not None:
            _required(self.execution_id, "execution_id")
        if self.event_version < 1:
            raise ValueError("event_version must be positive")
        if self.execution_id is None:
            if self.sequence is not None:
                raise ValueError("sequence requires execution_id")
        elif self.sequence is None or self.sequence < 1:
            raise ValueError("execution events require a positive sequence")
        _aware(self.occurred_at, "occurred_at")
        try:
            classification = DataClassification(self.classification)
        except ValueError as exc:
            raise ValueError("classification is invalid") from exc
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "payload", freeze_payload(self.payload))

    def __repr__(self) -> str:
        return (
            "EventEnvelope("
            f"event_id={self.event_id!r}, event_type={self.event_type!r}, "
            f"event_version={self.event_version}, sequence={self.sequence!r}, "
            f"execution_id={self.execution_id!r}, classification={self.classification.value!r})"
        )

    def context(self, *, purpose: str) -> EventContext:
        if self.agent_id is None or self.execution_id is None:
            raise ValueError("event does not contain an execution context")
        return EventContext(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            execution_id=self.execution_id,
            correlation_id=self.correlation_id,
            purpose=purpose,
        )


def classification_allows(clearance: DataClassification, classification: DataClassification) -> bool:
    return clearance_allows(clearance.value, classification.value)
