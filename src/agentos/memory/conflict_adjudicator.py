"""Optional small-model adjudicator for ambiguous memory conflicts (WS-D3 / D-RF6).

This module is strictly opt-in (gated by ``memory.conflicts.llmAdjudication``) and
NEVER generates or modifies memory text: it accepts only a strict JSON payload
``{"decision": "add|supersede|conflict|noop", "target_index": i}`` validated
against the candidate list bounds. Any error, timeout, malformed payload, or
out-of-range index safely degrades to ``conflict`` (``status="proposed"``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any

from .models_write import WriteDecision

VALID_ADJUDICATION_DECISIONS = frozenset({"add", "supersede", "conflict", "noop"})

ADJUDICATION_SYSTEM_PROMPT = (
    "You classify whether a candidate memory fact duplicates, supersedes, conflicts with, "
    "or is distinct from existing active memories in the same scope.\n"
    "Reply ONLY with a single JSON object matching this exact schema:\n"
    '{"decision": "add|supersede|conflict|noop", "target_index": 0}\n'
    "Rules:\n"
    "- NEVER output rewritten, merged, or new memory text.\n"
    "- Use `noop` when the candidate says the same thing as an existing memory.\n"
    "- Use `supersede` when the candidate clearly updates an existing memory of compatible authority.\n"
    "- Use `conflict` when the candidate contradicts an existing memory or is uncertain.\n"
    "- Use `add` (with `target_index: null`) when the candidate is a distinct, non-conflicting fact.\n"
    "- `target_index` must be a valid 0-based integer index into the existing memories list "
    "(or null only when decision is `add`)."
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced = _JSON_FENCE_RE.search(text)
    candidates_raw = [fenced.group(1)] if fenced else []
    candidates_raw.append(text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates_raw.append(text[start : end + 1])

    for snippet in candidates_raw:
        try:
            parsed = json.loads(snippet)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_adjudication_response(
    raw_text: str,
    candidate_count: int,
) -> tuple[str, int | None] | None:
    """Strictly parse ``{"decision": ..., "target_index": ...}`` and ignore any extra fields."""
    if candidate_count < 0:
        return None
    payload = _extract_json_object(raw_text)
    if payload is None:
        return None

    raw_decision = payload.get("decision")
    if not isinstance(raw_decision, str):
        return None
    decision = raw_decision.strip().lower()
    if decision not in VALID_ADJUDICATION_DECISIONS:
        return None

    raw_index = payload.get("target_index")
    if decision == "add":
        if raw_index is None:
            return ("add", None)
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            return None
        if not (0 <= raw_index < candidate_count):
            return None
        return ("add", None)

    if isinstance(raw_index, bool) or not isinstance(raw_index, int):
        return None
    if not (0 <= raw_index < candidate_count):
        return None
    return (decision, raw_index)


def _candidate_field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


class ConflictAdjudicator:
    """Consults a configured small model to adjudicate ambiguous memory conflicts.

    Follows the same provider ``.stream(request)`` convention used by
    ``agentos.memory.consolidation.MemoryConsolidator``.
    """

    def __init__(
        self,
        provider: Any | None = None,
        *,
        provider_name: str = "",
        model_id: str = "",
        max_output_tokens: int = 128,
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self.model_id = model_id
        self.max_output_tokens = max(32, int(max_output_tokens))

    def adjudicate(
        self,
        candidate_fact: str,
        existing_candidates: Sequence[Any],
        *,
        llm_adjudication_enabled: bool = False,
        candidate_source_kind: str = "agent",
        default_target_id: str | None = None,
    ) -> WriteDecision:
        fallback_target = default_target_id or (
            str(_candidate_field(existing_candidates[0], "memory_id", "") or "") or None
            if existing_candidates
            else None
        )

        # Hard guard: NEVER invoke the provider when the setting is disabled.
        if not llm_adjudication_enabled:
            return WriteDecision(
                action="conflict",
                target_id=fallback_target,
                status="proposed",
                reason="llm_adjudication_disabled",
            )

        if self.provider is None or not existing_candidates:
            return WriteDecision(
                action="conflict",
                target_id=fallback_target,
                status="proposed",
                reason="adjudicator_unavailable",
            )

        lines = [
            f"Candidate ({candidate_source_kind}): {candidate_fact}",
            "",
            "Existing active memories:",
        ]
        for idx, item in enumerate(existing_candidates):
            src = str(_candidate_field(item, "source_kind", "agent") or "agent")
            fact = str(_candidate_field(item, "fact", "") or "")
            lines.append(f"[{idx}] ({src}): {fact}")

        try:
            events = self.provider.stream(
                {
                    "turn_id": "memory-conflict-adjudication",
                    "provider": self.provider_name,
                    "model": self.model_id,
                    "messages": [
                        {"role": "system", "content": ADJUDICATION_SYSTEM_PROMPT},
                        {"role": "user", "content": "\n".join(lines)},
                    ],
                    "tools": [],
                    "max_output_tokens": self.max_output_tokens,
                }
            )
            raw_text = "".join(
                str(getattr(event, "text", "") or "")
                for event in events
                if str(getattr(event, "kind", "")) == "text"
            )
        except Exception:  # noqa: BLE001 - any model/network/timeout error degrades to conflict
            return WriteDecision(
                action="conflict",
                target_id=fallback_target,
                status="proposed",
                reason="adjudicator_error",
            )

        parsed = parse_adjudication_response(raw_text, len(existing_candidates))
        if parsed is None:
            return WriteDecision(
                action="conflict",
                target_id=fallback_target,
                status="proposed",
                reason="adjudicator_invalid_response",
            )

        decision, target_index = parsed
        if decision == "add":
            return WriteDecision(action="add", target_id=None, status="active", reason="llm_adjudication")

        assert target_index is not None
        chosen = existing_candidates[target_index]
        chosen_id = str(_candidate_field(chosen, "memory_id", "") or "") or fallback_target
        chosen_source = str(_candidate_field(chosen, "source_kind", "agent") or "agent")

        if decision == "noop":
            return WriteDecision(action="noop", target_id=chosen_id, status="active", reason="llm_adjudication")

        if decision == "supersede":
            # Defense in depth: an agent can NEVER supersede a user-authored memory.
            if chosen_source == "user" and candidate_source_kind != "user":
                return WriteDecision(
                    action="conflict",
                    target_id=chosen_id,
                    status="proposed",
                    reason="agent_vs_user",
                )
            return WriteDecision(
                action="supersede",
                target_id=chosen_id,
                status="active",
                reason="llm_adjudication",
            )

        return WriteDecision(
            action="conflict",
            target_id=chosen_id,
            status="proposed",
            reason="llm_adjudication",
        )


__all__ = [
    "ADJUDICATION_SYSTEM_PROMPT",
    "ConflictAdjudicator",
    "VALID_ADJUDICATION_DECISIONS",
    "parse_adjudication_response",
]
