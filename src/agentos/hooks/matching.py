"""Claude Code hook matchers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agentos.agentic.lifecycle import HookEvent

MAX_TARGET = 500
MATCH_FIELDS: dict[HookEvent, str] = {
    HookEvent.PRE_TOOL_USE: "tool_name",
    HookEvent.POST_TOOL_USE: "tool_name",
    HookEvent.POST_TOOL_USE_FAILURE: "tool_name",
    HookEvent.PERMISSION_REQUEST: "tool_name",
    HookEvent.PERMISSION_DENIED: "tool_name",
    HookEvent.SESSION_START: "source",
    HookEvent.SETUP: "trigger",
    HookEvent.SESSION_END: "reason",
    HookEvent.PRE_COMPACT: "trigger",
    HookEvent.POST_COMPACT: "trigger",
    HookEvent.NOTIFICATION: "notification_type",
    HookEvent.SUBAGENT_START: "agent_type",
    HookEvent.SUBAGENT_STOP: "agent_type",
    HookEvent.CONFIG_CHANGE: "source",
    HookEvent.INSTRUCTIONS_LOADED: "memory_type",
    HookEvent.ELICITATION: "mcp_server_name",
    HookEvent.ELICITATION_RESULT: "mcp_server_name",
}
_WORD = re.compile(r"[A-Za-z0-9_\-]+")
_WORDS = re.compile(r"[A-Za-z0-9_\-]+(\|[A-Za-z0-9_\-]+)+")


def matcher_applies(event: HookEvent, matcher: str, payload: Mapping[str, Any]) -> bool:
    pattern = str(matcher or "").strip()
    if pattern in {"", "*"}:
        return True
    field = MATCH_FIELDS.get(event)
    if field is None:
        return True
    target = str(payload.get(field) or "")
    if _WORD.fullmatch(pattern):
        return target == pattern
    if _WORDS.fullmatch(pattern):
        return target in pattern.split("|")
    if len(target) > MAX_TARGET:
        return False
    try:
        return re.fullmatch(pattern, target) is not None
    except re.error:
        return False
