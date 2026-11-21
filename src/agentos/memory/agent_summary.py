"""Deterministic frozen prompt summary builder for per-agent memory (WS-D5 / D-RF9).

Domain-only module: renders a fixed, ordered section injected once at subagent start so
mid-run memory writes never invalidate prompt caching.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_AGENT_SUMMARY_MAX_CHARS = 4000

_KIND_PRIORITY: Mapping[str, int] = {
    "preference": 0,
    "architecture": 1,
    "operational": 2,
    "episodic": 3,
    "fact": 4,
}


def _normalize_fact(raw: object) -> str:
    return " ".join(str(raw or "").split())


def _is_active_row(item: Mapping[str, Any]) -> bool:
    if item.get("superseded_by") is not None:
        return False
    status = str(item.get("status") or "active").strip().lower()
    return status == "active"


def _sort_key(item: Mapping[str, Any]) -> tuple[int, float, str, str]:
    kind = str(item.get("kind") or "fact").strip().lower()
    kind_rank = _KIND_PRIORITY.get(kind, 5)
    try:
        confidence = float(item.get("confidence") if item.get("confidence") is not None else 1.0)
    except (TypeError, ValueError):
        confidence = 1.0
    fact = _normalize_fact(item.get("fact"))
    memory_id = str(item.get("memory_id") or "")
    return (kind_rank, -round(confidence, 4), fact.casefold(), memory_id)


def freeze_summary(
    memories: Sequence[Mapping[str, Any]],
    agent_name: str,
    max_chars: int = DEFAULT_AGENT_SUMMARY_MAX_CHARS,
) -> str:
    """Build a deterministic frozen prompt section for ``agent_name`` capped strictly at ``max_chars``."""
    budget = int(max_chars)
    if budget <= 0 or not memories:
        return ""

    clean_name = " ".join(str(agent_name or "").split()) or "agent"
    active_items = [
        item
        for item in memories
        if isinstance(item, Mapping) and _is_active_row(item) and _normalize_fact(item.get("fact"))
    ]
    if not active_items:
        return ""

    ordered = sorted(active_items, key=_sort_key)
    header = f"## O que o agente {clean_name} aprendeu"
    if len(header) >= budget:
        return header[:budget]

    lines: list[str] = [header]
    used_chars = len(header)
    seen_facts: set[str] = set()

    for item in ordered:
        fact = _normalize_fact(item.get("fact"))
        norm_key = fact.casefold()
        if not fact or norm_key in seen_facts:
            continue
        seen_facts.add(norm_key)

        kind = str(item.get("kind") or "fact").strip().lower()
        prefix = f"- ({kind}) " if kind and kind != "fact" else "- "
        entry = f"{prefix}{fact}"
        needed = 1 + len(entry)  # leading newline + entry

        if used_chars + needed <= budget:
            lines.append(entry)
            used_chars += needed
            continue

        # If not even the first item fit whole, truncate it cleanly within the remaining budget.
        if len(lines) == 1:
            remaining = budget - used_chars - 1  # reserve '\n'
            if remaining > len(prefix) + 3:
                clipped = fact[: max(1, remaining - len(prefix) - 1)].rstrip()
                lines.append(f"{prefix}{clipped}…")
        break

    if len(lines) == 1:
        return ""
    result = "\n".join(lines)
    return result[:budget]


__all__ = ["DEFAULT_AGENT_SUMMARY_MAX_CHARS", "freeze_summary"]
