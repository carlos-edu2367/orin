from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .models import _APPROVAL_RAW_KEYS, _TEXT_DELTA_RAW_KEYS, sanitize_public_mapping, sanitize_summary


MAX_ACTIVITY_SUMMARY = 512


class AgentActivityEventType(StrEnum):
    TURN_STARTED = "turn.started"
    MODEL_ROUTING_STARTED = "model.routing_started"
    CONTEXT_UPDATED = "context.updated"
    CONTEXT_COMPACTED = "context.compacted"
    ASSISTANT_DELTA = "assistant.delta"
    AGENT_THOUGHT_DELTA = "agent.thought_delta"
    ASSISTANT_COMPLETED = "assistant.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_PROGRESSED = "tool.progressed"
    TOOL_FINISHED = "tool.finished"
    ARTIFACT_CREATED = "artifact.created"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    TERMINAL_STARTED = "terminal.started"
    TERMINAL_OUTPUT_SUMMARY = "terminal.output_summary"
    BROWSER_STARTED = "browser.started"
    BROWSER_NAVIGATED = "browser.navigated"
    BROWSER_ARTIFACT = "browser.artifact"
    AGENT_CREATED = "agent.created"
    MEMORY_LEARNED = "memory.learned"
    AGENT_MESSAGE_SENT = "agent.message_sent"
    AGENT_MESSAGE_RECEIVED = "agent.message_received"
    DELEGATION_CREATED = "delegation.created"
    DELEGATION_WAITING = "delegation.waiting"
    DELEGATION_COMPLETED = "delegation.completed"
    DELEGATION_FAILED = "delegation.failed"
    TURN_WAITING_USER = "turn.waiting_user"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    CODE_MODE_ACTIVATED = "code_mode.activated"
    CODE_MODE_STAGE_CHANGED = "code_mode.stage_changed"
    CODE_MODE_PLAN_READY = "code_mode.plan_ready"
    CODE_MODE_VALIDATION_STARTED = "code_mode.validation_started"
    CODE_MODE_VALIDATION_FINISHED = "code_mode.validation_finished"
    CODE_MODE_COMPLETED = "code_mode.completed"
    CODE_MODE_COMPLETED_WITH_CAVEATS = "code_mode.completed_with_caveats"
    CODE_MODE_DECISION_REQUIRED = "code_mode.decision_required"
    WORKSPACE_TRUST_REQUIRED = "workspace.trust_required"
    CODE_MODE_BLOCKED = "code_mode.blocked"
    TOOL_PERMISSION_REQUIRED = "tool.permission_required"
    PLUGIN_HOOK_EXECUTED = "plugin.hook_executed"
    NOTIFICATION_PUSHED = "notification.pushed"
    # W6: a question for the SDK host behind `orin code` (its body is in the turn's CLI transcript).
    HOST_REQUEST = "host.request"
    # Orin Friend & Live Voice Mode events
    SPEAK_BLOCK_STARTED = "speak.block_started"
    SPEAK_DELTA = "speak.delta"
    SPEAK_CHUNK_READY = "speak.chunk_ready"
    SPEAK_FILLER_TRIGGERED = "speak.filler_triggered"
    SPEAK_INTERRUPTED = "speak.interrupted"
    SPEAK_FINISHED = "speak.finished"
    CODE_SESSION_PROGRESS = "code_session.progress"


class ActivityVisibility(StrEnum):
    PUBLIC = "public"
    OWNER = "owner"
    INTERNAL = "internal"


_APPROVAL_EVENT_TYPES = frozenset({AgentActivityEventType.CODE_MODE_DECISION_REQUIRED, AgentActivityEventType.TOOL_PERMISSION_REQUIRED})
_TEXT_DELTA_EVENT_TYPES = frozenset({AgentActivityEventType.ASSISTANT_DELTA, AgentActivityEventType.AGENT_THOUGHT_DELTA})
_SPEAK_EVENT_TYPES = frozenset({
    AgentActivityEventType.SPEAK_BLOCK_STARTED,
    AgentActivityEventType.SPEAK_DELTA,
    AgentActivityEventType.SPEAK_CHUNK_READY,
    AgentActivityEventType.SPEAK_FILLER_TRIGGERED,
    AgentActivityEventType.SPEAK_INTERRUPTED,
    AgentActivityEventType.SPEAK_FINISHED,
})
_SPEAK_RAW_KEYS = _TEXT_DELTA_RAW_KEYS | frozenset({
    "raw_text",
    "clean_text",
    "tts_text",
    "raw_tts_text",
    "normalized_text",
    "speak_delta",
    "text",
})


@dataclass(frozen=True, slots=True)
class AgentActivityEvent:
    event_id: str
    conversation_id: str
    turn_id: str
    execution_id: str
    user_id: str
    agent_id: str
    event_type: AgentActivityEventType
    sequence: int
    summary: str
    payload: Mapping[str, object]
    created_at: datetime
    workspace_id: str | None = None
    parent_agent_id: str | None = None
    visibility: ActivityVisibility = ActivityVisibility.PUBLIC

    def __post_init__(self) -> None:
        for field in (
            "event_id",
            "conversation_id",
            "turn_id",
            "execution_id",
            "user_id",
            "agent_id",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-blank")
        for field in ("workspace_id", "parent_agent_id"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} must be non-blank when supplied")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("summary must be non-blank")
        if len(self.summary) > MAX_ACTIVITY_SUMMARY:
            raise ValueError("summary exceeds limit")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        try:
            event_type = AgentActivityEventType(self.event_type)
            visibility = ActivityVisibility(str(self.visibility).lower())
        except ValueError as exc:
            raise ValueError("event_type or visibility is invalid") from exc
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "summary", sanitize_summary(self.summary))
        if event_type in _APPROVAL_EVENT_TYPES:  # W7-R1: the person approves what these payloads show
            raw_keys = _APPROVAL_RAW_KEYS
        elif event_type in _TEXT_DELTA_EVENT_TYPES:  # W7-R3: the model's own text is not redacted
            raw_keys = _TEXT_DELTA_RAW_KEYS
        elif event_type in _SPEAK_EVENT_TYPES:
            raw_keys = _SPEAK_RAW_KEYS
        else:
            raw_keys = frozenset()
        object.__setattr__(self, "payload", sanitize_public_mapping(self.payload, raw_keys=raw_keys))

    def __repr__(self) -> str:
        return (
            "AgentActivityEvent("
            f"event_id={self.event_id!r}, event_type={self.event_type.value!r}, "
            f"conversation_id={self.conversation_id!r}, sequence={self.sequence})"
        )


__all__ = [
    "ActivityVisibility",
    "AgentActivityEvent",
    "AgentActivityType",
    "AgentActivityEventType",
    "MAX_ACTIVITY_SUMMARY",
]

AgentActivityType = AgentActivityEventType
