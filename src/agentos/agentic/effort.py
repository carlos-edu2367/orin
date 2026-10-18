"""Reasoning effort: Anthropic thinking budget, OpenAI-style reasoning effort; other providers ignore it."""

from __future__ import annotations

import re
from collections.abc import Iterable, MutableMapping
from typing import Any

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
THINKING_BUDGET = {"low": 2_048, "medium": 6_000, "high": 12_000, "xhigh": 20_000, "max": 28_000}
THINKING_HEADROOM = 4_000
OPENAI_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
_ANTHROPIC_THINKING_MODEL = re.compile(r"claude-(?:(?:opus|sonnet|haiku)-[4-9]|[4-9]|3-7)")
_OPENAI_REASONING_MODEL = re.compile(r"^(?:o[1-9]|gpt-[5-9])")


def normalize_effort(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return level if level in EFFORT_LEVELS else None


def reasoning_supported(provider: str, model_id: str, capabilities: Iterable[str]) -> bool:
    """Whether a reasoning control can be sent to this model without being rejected."""
    names = {str(item).strip().lower() for item in capabilities or ()}
    model = str(model_id or "").strip().lower().lstrip("~").rsplit("/", 1)[-1]
    name = str(provider or "").lower()
    if name in {"openrouter", "omniroute"}:
        return "reasoning" in names
    if name == "anthropic":
        return any("thinking" in item for item in names) or bool(_ANTHROPIC_THINKING_MODEL.search(model))
    if name == "openai":
        return bool(_OPENAI_REASONING_MODEL.match(model))
    return False


def apply_effort(provider: str, payload: MutableMapping[str, Any], effort: Any) -> MutableMapping[str, Any]:
    """Add the provider's reasoning control to a request payload (in place) and return it."""
    level = normalize_effort(effort)
    if level is None:
        return payload
    name = str(provider or "").lower()
    if name == "anthropic":
        budget = THINKING_BUDGET[level]
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        current = payload.get("max_tokens")
        payload["max_tokens"] = max(current if isinstance(current, int) and not isinstance(current, bool) else 0, budget + THINKING_HEADROOM)
    elif name == "openrouter":
        payload["reasoning"] = {"effort": OPENAI_EFFORT[level]}
    elif name in {"openai", "omniroute"}:
        payload["reasoning_effort"] = OPENAI_EFFORT[level]
    return payload


__all__ = ["EFFORT_LEVELS", "OPENAI_EFFORT", "THINKING_BUDGET", "apply_effort", "normalize_effort", "reasoning_supported"]
