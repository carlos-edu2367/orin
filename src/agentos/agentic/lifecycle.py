"""Typed lifecycle events at the points Claude Code exposes as hooks.

W0 only emits. W2 subscribes the hook engine and applies the returned
decisions. Emitting is always safe: a failing subscriber is logged and
skipped, never raised into the turn.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(StrEnum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    POST_TOOL_BATCH = "PostToolBatch"
    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    USER_PROMPT_EXPANSION = "UserPromptExpansion"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    PRE_MODEL_SWITCH = "PreModelSwitch"
    POST_MODEL_SWITCH = "PostModelSwitch"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    CONFIG_CHANGE = "ConfigChange"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"
    DIRECTORY_ADDED = "DirectoryAdded"
    MESSAGE_DISPLAY = "MessageDisplay"
    STATUS_LINE = "StatusLine"
    FILE_SUGGESTION = "FileSuggestion"


CLAUDE_HOOK_EVENTS: tuple[HookEvent, ...] = tuple(
    event for event in HookEvent if event not in {HookEvent.STATUS_LINE, HookEvent.FILE_SUGGESTION}
)

NON_BLOCKING_EVENTS: frozenset[HookEvent] = frozenset({
    HookEvent.NOTIFICATION, HookEvent.SESSION_START, HookEvent.SESSION_END, HookEvent.SETUP,
    HookEvent.STOP_FAILURE, HookEvent.SUBAGENT_START, HookEvent.POST_TOOL_USE_FAILURE,
    HookEvent.POST_COMPACT, HookEvent.POST_MODEL_SWITCH, HookEvent.PERMISSION_DENIED,
    HookEvent.WORKTREE_CREATE, HookEvent.WORKTREE_REMOVE, HookEvent.INSTRUCTIONS_LOADED,
    HookEvent.CWD_CHANGED, HookEvent.FILE_CHANGED, HookEvent.DIRECTORY_ADDED,
    HookEvent.MESSAGE_DISPLAY, HookEvent.STATUS_LINE, HookEvent.FILE_SUGGESTION,
})

# Non-blocking events that may still inject context into the conversation.
_CONTEXT_EVENTS = frozenset({HookEvent.SESSION_START, HookEvent.SUBAGENT_START, HookEvent.POST_TOOL_USE_FAILURE})
_DECISION_RANK = {"allow": 1, "ask": 2, "deny": 3}
# W5: an answer to an MCP elicitation; the more cautious one wins when hooks disagree.
_ELICITATION_RANK = {"accept": 1, "decline": 2, "cancel": 3}


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    name: HookEvent
    payload: Mapping[str, Any]


@dataclass(slots=True)
class LifecycleDecision:
    permission_decision: str | None = None
    reason: str | None = None
    updated_input: dict[str, Any] | None = None
    updated_tool_output: Any = None
    blocked: bool = False
    stop: bool = False
    stop_reason: str | None = None
    additional_context: list[str] = field(default_factory=list)
    system_messages: list[str] = field(default_factory=list)
    elicitation: dict[str, Any] | None = None  # W5: {"action": accept|decline|cancel, "content": {...}}

    def merge(self, other: "LifecycleDecision") -> None:
        if other.elicitation is not None and (
            self.elicitation is None
            or _ELICITATION_RANK.get(str(other.elicitation.get("action")), 0) > _ELICITATION_RANK.get(str(self.elicitation.get("action")), 0)
        ):
            self.elicitation = dict(other.elicitation)
        incoming = other.permission_decision if other.permission_decision in _DECISION_RANK else None
        if incoming is not None and _DECISION_RANK[incoming] > _DECISION_RANK.get(self.permission_decision or "", 0):
            self.permission_decision = incoming
            self.reason = other.reason or self.reason
        elif other.reason and not self.reason:
            self.reason = other.reason
        if self.updated_input is None and other.updated_input is not None:
            self.updated_input = dict(other.updated_input)
        if self.updated_tool_output is None and other.updated_tool_output is not None:
            self.updated_tool_output = other.updated_tool_output
        self.blocked = self.blocked or other.blocked
        if other.stop and not self.stop:
            self.stop = True
            self.stop_reason = other.stop_reason
        self.additional_context.extend(item for item in other.additional_context if item)
        self.system_messages.extend(item for item in other.system_messages if item)


Subscriber = Callable[[LifecycleEvent], object]


class LifecycleBus:
    def __init__(self, *, base: Mapping[str, Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Subscriber] = []
        self._base: dict[str, Any] = {"transcript_path": ""}
        self._base.update(dict(base or {}))

    def bind(self, **values: Any) -> None:
        with self._lock:
            self._base.update({key: value for key, value in values.items() if value is not None})

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def emit(self, name: HookEvent | str, /, **payload: Any) -> LifecycleDecision:
        event_name = HookEvent(name)
        with self._lock:
            subscribers = list(self._subscribers)
            body = dict(self._base)
        body.update(payload)
        body["hook_event_name"] = event_name.value
        event = LifecycleEvent(event_name, MappingProxyType(body))
        decision = LifecycleDecision()
        for subscriber in subscribers:
            try:
                result = subscriber(event)
            except Exception:  # noqa: BLE001 - a subscriber never breaks the turn
                logger.exception("lifecycle subscriber failed for %s", event_name.value)
                continue
            if isinstance(result, LifecycleDecision):
                decision.merge(result)
        if event_name in NON_BLOCKING_EVENTS:
            return LifecycleDecision(
                additional_context=decision.additional_context if event_name in _CONTEXT_EVENTS else [],
                system_messages=decision.system_messages,
            )
        return decision
