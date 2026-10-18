"""Claude Code hook protocol: exit codes, JSON control output, prompt/agent verdicts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentos.agentic.lifecycle import HookEvent, LifecycleDecision

_CONTEXT_FROM_STDOUT = frozenset({HookEvent.SESSION_START, HookEvent.USER_PROMPT_SUBMIT, HookEvent.SUBAGENT_START})


@dataclass(frozen=True, slots=True)
class HookRun:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None


def _text(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value.strip() else None


def _blocking(event: HookEvent, reason: str) -> LifecycleDecision:
    decision = LifecycleDecision(blocked=True, reason=reason[:4000])
    if event is HookEvent.PRE_TOOL_USE:
        decision.permission_decision = "deny"
    return decision


def decision_from_json(event: HookEvent, data: Mapping[str, Any]) -> LifecycleDecision:
    decision = LifecycleDecision()
    if data.get("continue") is False:
        decision.stop = True
        decision.stop_reason = _text(data.get("stopReason"), 2000) or "stopped by hook"
    message = _text(data.get("systemMessage"), 2000)
    if message:
        decision.system_messages.append(message)
    verdict = data.get("decision")
    if verdict == "block":
        blocked = _blocking(event, _text(data.get("reason"), 4000) or "blocked by hook")
        decision.blocked, decision.reason, decision.permission_decision = True, blocked.reason, blocked.permission_decision
    elif verdict == "approve" and event is HookEvent.PRE_TOOL_USE:
        decision.permission_decision = "allow"
    specific = data.get("hookSpecificOutput")
    if isinstance(specific, Mapping):
        context = _text(specific.get("additionalContext"), 10_000)
        if context:
            decision.additional_context.append(context)
        if event is HookEvent.PRE_TOOL_USE:
            permission = specific.get("permissionDecision")
            if permission in {"allow", "deny", "ask"}:
                decision.permission_decision = permission
                decision.reason = _text(specific.get("permissionDecisionReason"), 4000) or decision.reason
            updated = specific.get("updatedInput")
            if isinstance(updated, Mapping):
                decision.updated_input = dict(updated)
        elif event is HookEvent.PERMISSION_REQUEST:
            answer = specific.get("decision")
            if isinstance(answer, Mapping):
                if answer.get("behavior") in {"allow", "deny"}:
                    decision.permission_decision = answer["behavior"]
                updated = answer.get("updatedInput")
                if isinstance(updated, Mapping):
                    decision.updated_input = dict(updated)
                decision.reason = _text(answer.get("message"), 4000) or decision.reason
                if answer.get("interrupt") is True:
                    decision.stop = True
                    decision.stop_reason = decision.reason or "interrupted by hook"
        elif event is HookEvent.POST_TOOL_USE and "updatedMCPToolOutput" in specific:
            decision.updated_tool_output = specific["updatedMCPToolOutput"]
    return decision


def decision_from_command(event: HookEvent, run: HookRun, *, on_failure: str = "ignore") -> LifecycleDecision:
    if run.error or run.timed_out or run.exit_code is None:
        if on_failure == "block":
            return _blocking(event, run.error or ("hook timed out" if run.timed_out else "hook failed"))
        return LifecycleDecision()
    if run.exit_code == 2:
        return _blocking(event, run.stderr.strip() or "blocked by hook")
    if run.exit_code != 0:
        if on_failure == "block":
            return _blocking(event, run.stderr.strip() or f"hook exited with {run.exit_code}")
        return LifecycleDecision()
    text = run.stdout.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, Mapping):
            return decision_from_json(event, data)
        return LifecycleDecision()
    decision = LifecycleDecision()
    if text and event in _CONTEXT_FROM_STDOUT:
        decision.additional_context.append(text[:10_000])
    return decision


def parse_verdict(text: str) -> dict[str, Any] | None:
    found: list[dict[str, Any]] = []
    for match in re.finditer(r"\{[^{}]*\}", str(text or ""), re.DOTALL):
        try:
            value = json.loads(match.group(0))
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("ok"), bool):
            found.append(value)
    if not found:
        return None
    # Fail closed on disagreement: text that echoes a forged {"ok": true} must not cancel a real refusal.
    refusals = [item for item in found if item["ok"] is False]
    return refusals[-1] if refusals else found[-1]


def decision_from_verdict(event: HookEvent, verdict: Mapping[str, Any] | None, *, on_failure: str = "ignore") -> LifecycleDecision:
    if not isinstance(verdict, Mapping) or not isinstance(verdict.get("ok"), bool):
        return _blocking(event, "hook gave no verdict") if on_failure == "block" else LifecycleDecision()
    if verdict["ok"]:
        return LifecycleDecision()
    return _blocking(event, _text(verdict.get("reason"), 4000) or "rejected by hook")
