"""An MCP server asking the person for input during a call (elicitation, W5).

Orin has no input card yet (W7), so a request nobody answers is declined. ``Elicitation`` hooks may answer it
(``hookSpecificOutput.action`` ``accept`` with ``content``, ``decline`` or ``cancel``; a blocking hook declines);
``ElicitationResult`` hooks see the answer and may change it the same way. An accepted answer must be a flat
object of strings, numbers, booleans or lists of strings; anything else is declined.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from agentos.agentic.lifecycle import HookEvent

logger = logging.getLogger(__name__)

MAX_ANSWER_FIELDS = 50
MAX_ANSWER_TEXT = 10_000
_ACTIONS = ("accept", "decline", "cancel")


def checked_answer(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """A valid answer for the server, or a decline."""
    if not isinstance(raw, Mapping) or raw.get("action") not in _ACTIONS:
        return {"action": "decline"}
    if raw["action"] != "accept":
        return {"action": raw["action"]}
    content = raw.get("content")
    if not isinstance(content, Mapping) or len(content) > MAX_ANSWER_FIELDS:
        return {"action": "decline"}
    clean: dict[str, Any] = {}
    for key, value in content.items():
        if not isinstance(key, str):
            return {"action": "decline"}
        if isinstance(value, str):
            clean[key] = value[:MAX_ANSWER_TEXT]
        elif isinstance(value, (bool, int, float)):
            clean[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            clean[key] = [item[:MAX_ANSWER_TEXT] for item in value[:100]]
        else:
            return {"action": "decline"}
    return {"action": "accept", "content": clean}


def answer_elicitation(bus: Any, server: str, params: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """``(answer, answered_by_a_hook)`` for one elicitation request from ``server``."""
    answer: dict[str, Any] = {"action": "decline"}
    if bus is None:
        return answer, False
    message = str(params.get("message") or "")[:4000]
    schema = params.get("requestedSchema") if isinstance(params.get("requestedSchema"), Mapping) else {}
    answered = False
    try:
        decision = bus.emit(HookEvent.ELICITATION, mcp_server_name=server, message=message, requested_schema=dict(schema), mode="form")
        if decision.blocked:
            answered = True
        elif decision.elicitation is not None:
            answer, answered = checked_answer(decision.elicitation), True
        result = bus.emit(
            HookEvent.ELICITATION_RESULT, mcp_server_name=server, action=answer["action"], content=dict(answer.get("content") or {}),
        )
        if result.blocked:
            answer, answered = {"action": "decline"}, True
        elif result.elicitation is not None:
            answer, answered = checked_answer(result.elicitation), True
    except Exception:  # noqa: BLE001 - a failing hook declines, it never breaks the call
        logger.exception("elicitation hooks failed for %s", server)
        return {"action": "decline"}, False
    return answer, answered


__all__ = ["answer_elicitation", "checked_answer"]
