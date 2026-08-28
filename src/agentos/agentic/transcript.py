"""The agentic trajectory of a turn, stored provider-neutrally.

A turn's tool calls and their results used to exist only inside the loop's
in-memory message list. When the turn ended they were gone: the next message
in the same conversation started from the plain user/assistant transcript
plus a twenty-line ledger of truncated summaries. Asking the agent to revise
work it had just done meant asking it to rediscover that work first.

This module is the shape those steps are stored in and the rules for putting
them back. It is deliberately provider-neutral: a step recorded while talking
to Anthropic must rehydrate correctly when the person switches the
conversation to an OpenAI-compatible model, so the provider shape is applied
at read time, never at write time.
"""
from __future__ import annotations

import json
from typing import Iterable, Mapping, Sequence


STEP_ASSISTANT_TOOL_CALL = "assistant_tool_call"
STEP_TOOL_RESULT = "tool_result"
STEP_KINDS = (STEP_ASSISTANT_TOOL_CALL, STEP_TOOL_RESULT)

# A tool result already arrives bounded at MAX_TOOL_RESULT_CHARS (12k), so
# this is a safety net for anything that grows past it rather than the
# operative limit. It is larger than the execution journal's 12k copy because
# that record serves recovery and this one serves the agent's memory.
MAX_STEP_CHARS = 32_768

# How much of the turn's context budget rehydrated history may occupy. The
# rest belongs to the system prompt, the tool schemas and the current
# exchange. Filling from the most recent step backwards means an old turn is
# what gets dropped, never the one the person is following up on.
REHYDRATION_BUDGET_FRACTION = 0.40


def estimated_tokens(value: object) -> int:
    """Cheap upper-bound estimate; four characters per token is the usual rule.

    Matches the estimate the runtime's own trimming uses, so a step that fits
    the rehydration budget is not immediately trimmed back out.
    """
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return max(1, len(payload) // 4)


def bound(content: str) -> tuple[str, int, bool]:
    """Return the storable content, its original size, and whether it was cut."""
    original = len(content)
    if original <= MAX_STEP_CHARS:
        return content, original, False
    return content[:MAX_STEP_CHARS], original, True


def assistant_tool_call_payload(text: str, calls: Iterable[Mapping[str, str]]) -> dict[str, object]:
    return {
        "text": text,
        "calls": [
            {"id": str(call.get("id") or ""), "name": str(call.get("name") or ""), "arguments": str(call.get("arguments") or "")}
            for call in calls
        ],
    }


def tool_result_payload(*, call_id: str, name: str, status: str, content: str) -> dict[str, object]:
    stored, original, truncated = bound(content)
    return {
        "id": call_id, "name": name, "status": status, "content": stored,
        "content_bytes": original, "truncated": truncated,
    }


def assistant_message(provider: str, text: str, calls: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """One assistant message carrying tool calls, in the provider's shape."""
    if provider.lower() == "anthropic":
        blocks: list[dict[str, object]] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for call in calls:
            raw = call.get("arguments")
            try:
                arguments = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, Mapping) else {})
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            blocks.append({"type": "tool_use", "id": str(call.get("id") or ""), "name": str(call.get("name") or ""), "input": arguments})
        return {"role": "assistant", "content": blocks}
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {"id": str(call.get("id") or ""), "type": "function",
             "function": {"name": str(call.get("name") or ""), "arguments": str(call.get("arguments") or "")}}
            for call in calls
        ],
    }


def tool_result_message(provider: str, payload: Mapping[str, object]) -> dict[str, object]:
    """One tool result, in the provider's shape.

    A result that was truncated on the way in says so, with its real size and
    the tool that produced it, so the model can decide whether re-running it
    is worth the tokens instead of assuming it has the whole thing.
    """
    content = str(payload.get("content") or "")
    if payload.get("truncated"):
        content += (
            f"\n[truncado: {payload.get('content_bytes')} caracteres no total; "
            f"chame {payload.get('name') or 'a ferramenta'} novamente se precisar do resto]"
        )
    if provider.lower() == "anthropic":
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(payload.get("id") or ""), "content": content}]}
    return {"role": "tool", "tool_call_id": str(payload.get("id") or ""), "content": content}


def project(steps: Sequence[Mapping[str, object]], provider: str) -> list[dict[str, object]]:
    """Turn stored steps into provider messages, in recorded order.

    Images are deliberately not rehydrated. A screenshot or a page render was
    worth its tokens on the iteration that asked for it; re-sending it on
    every later turn is pure cost, and the accompanying text already says what
    was seen.
    """
    messages: list[dict[str, object]] = []
    for step in steps:
        payload = step.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if step.get("kind") == STEP_ASSISTANT_TOOL_CALL:
            calls = payload.get("calls")
            if not isinstance(calls, list) or not calls:
                continue
            messages.append(assistant_message(provider, str(payload.get("text") or ""), calls))
        elif step.get("kind") == STEP_TOOL_RESULT:
            messages.append(tool_result_message(provider, payload))
    return messages


def units(steps: Sequence[Mapping[str, object]]) -> list[list[Mapping[str, object]]]:
    """Group each tool-calling step with the results that answer it.

    Both providers reject an orphaned tool result, so a call and its results
    are only ever kept or dropped together.
    """
    grouped: list[list[Mapping[str, object]]] = []
    for step in steps:
        if step.get("kind") == STEP_ASSISTANT_TOOL_CALL or not grouped:
            grouped.append([step])
        else:
            grouped[-1].append(step)
    # A leading run of results with no call before it cannot be replayed.
    if grouped and grouped[0] and grouped[0][0].get("kind") != STEP_ASSISTANT_TOOL_CALL:
        grouped.pop(0)
    return grouped


def within_budget(steps: Sequence[Mapping[str, object]], budget_tokens: int) -> list[Mapping[str, object]]:
    """The most recent whole units that fit, in original order.

    Filling from the end keeps the trajectory the person is following up on
    and drops the oldest, which is the opposite of what a naive head-first
    cut would do.
    """
    if budget_tokens <= 0:
        return []
    kept: list[list[Mapping[str, object]]] = []
    remaining = budget_tokens
    for unit in reversed(units(steps)):
        cost = sum(estimated_tokens(step.get("payload")) for step in unit)
        if cost > remaining:
            break
        remaining -= cost
        kept.append(unit)
    return [step for unit in reversed(kept) for step in unit]


__all__ = [
    "MAX_STEP_CHARS", "REHYDRATION_BUDGET_FRACTION", "STEP_ASSISTANT_TOOL_CALL", "STEP_KINDS",
    "STEP_TOOL_RESULT", "assistant_message", "assistant_tool_call_payload", "bound",
    "estimated_tokens", "project", "tool_result_message", "tool_result_payload", "units",
    "within_budget",
]
