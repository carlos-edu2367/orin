from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from types import MappingProxyType


MAX_ACTION_DEPTH = 4
MAX_ACTION_ITEMS = 64
MAX_ACTION_TEXT = 512
REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "credential",
    "cookie",
    "authorization",
    "raw_prompt",
    "prompt",
    "prompt_text",
    "raw_args",
    "raw_arguments",
    "raw_input",
    "raw_output",
    "provider_payload",
    "stdout",
    "stderr",
)


class AgentActionKind(StrEnum):
    TOOL = "tool"
    DELEGATION = "delegation"


def _required(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-blank")


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    return normalized in {"token", "tokens", "headers", "args", "arguments", "input", "output"} or any(
        part in normalized or part.replace("_", "") in compact for part in _SENSITIVE_KEY_PARTS
    )


_SENSITIVE_SUMMARY = re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|credential|cookie|authorization|prompt|(?:tool_)?args|provider[_-]?output|raw[_-]?(?:input|output|prompt))\s*[:=]\s*[^\s,;]+")


def sanitize_summary(value: str) -> str:
    return _SENSITIVE_SUMMARY.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def _bounded(value: object, *, depth: int, count: list[int], redact: bool) -> object:
    if depth > MAX_ACTION_DEPTH:
        raise ValueError("payload exceeds nesting limit")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_ACTION_TEXT:
            raise ValueError("payload text exceeds limit")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_ACTION_ITEMS:
            raise ValueError("payload contains too many entries")
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("payload keys must be non-blank strings")
            count[0] += 1
            if count[0] > MAX_ACTION_ITEMS:
                raise ValueError("payload exceeds item limit")
            result[key] = REDACTED if redact and _sensitive_key(key) else _bounded(
                item, depth=depth + 1, count=count, redact=redact
            )
        return MappingProxyType(result)
    if isinstance(value, (tuple, list)):
        if len(value) > MAX_ACTION_ITEMS:
            raise ValueError("payload contains too many entries")
        count[0] += len(value)
        if count[0] > MAX_ACTION_ITEMS:
            raise ValueError("payload exceeds item limit")
        return tuple(_bounded(item, depth=depth + 1, count=count, redact=redact) for item in value)
    raise ValueError("payload contains an unsupported value")


def freeze_action_input(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("input must be a mapping")
    result = _bounded(value, depth=0, count=[0], redact=False)
    assert isinstance(result, Mapping)
    return result


def sanitize_public_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("payload must be a mapping")
    result = _bounded(value, depth=0, count=[0], redact=True)
    assert isinstance(result, Mapping)
    return result


@dataclass(frozen=True, slots=True)
class AgentActionRequest:
    action_id: str
    turn_id: str
    agent_id: str
    kind: AgentActionKind
    tool_ref: str | None
    delegation_ref: str | None
    input: Mapping[str, object]
    deadline: datetime
    policy_context: Mapping[str, object]
    idempotency_key: str

    def __init__(
        self,
        action_id: str,
        turn_id: str,
        agent_id: str,
        kind: AgentActionKind,
        input: Mapping[str, object],
        deadline: datetime,
        policy_context: Mapping[str, object],
        idempotency_key: str,
        tool_ref: str | None = None,
        delegation_ref: str | None = None,
    ) -> None:
        for field, value in (
            ("action_id", action_id),
            ("turn_id", turn_id),
            ("agent_id", agent_id),
            ("idempotency_key", idempotency_key),
        ):
            _required(value, field)
        try:
            action_kind = AgentActionKind(str(kind).lower())
        except ValueError as exc:
            raise ValueError("kind is invalid") from exc
        if (tool_ref is None) == (delegation_ref is None):
            raise ValueError("exactly one action target is required")
        if action_kind is AgentActionKind.TOOL and tool_ref is None:
            raise ValueError("tool action requires tool_ref")
        if action_kind is AgentActionKind.DELEGATION and delegation_ref is None:
            raise ValueError("delegation action requires delegation_ref")
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("deadline must be timezone-aware")
        _required(tool_ref, "tool_ref") if tool_ref is not None else None
        _required(delegation_ref, "delegation_ref") if delegation_ref is not None else None
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "kind", action_kind)
        object.__setattr__(self, "tool_ref", tool_ref)
        object.__setattr__(self, "delegation_ref", delegation_ref)
        object.__setattr__(self, "input", freeze_action_input(input))
        object.__setattr__(self, "deadline", deadline)
        object.__setattr__(self, "policy_context", freeze_action_input(policy_context))
        object.__setattr__(self, "idempotency_key", idempotency_key)

    @property
    def typed_input(self) -> Mapping[str, object]:
        return self.input

    def public_payload(self) -> Mapping[str, object]:
        values: dict[str, object] = {
            "action_ref": self.action_id,
            "kind": self.kind.value,
        }
        if self.tool_ref is not None:
            values["tool_ref"] = self.tool_ref
        if self.delegation_ref is not None:
            values["delegation_ref"] = self.delegation_ref
        return sanitize_public_mapping(values)


__all__ = [
    "AgentActionKind",
    "AgentActionRequest",
    "MAX_ACTION_DEPTH",
    "MAX_ACTION_ITEMS",
    "MAX_ACTION_TEXT",
    "REDACTED",
    "freeze_action_input",
    "sanitize_public_mapping",
]
