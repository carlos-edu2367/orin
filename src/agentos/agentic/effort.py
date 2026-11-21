"""Reasoning effort: Anthropic adaptive/legacy thinking, OpenAI-style reasoning effort; other providers ignore it."""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from typing import Any

from .effort_levels import EffortAdjustment, ModelEffortInfo, clamp, levels_for

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")
THINKING_BUDGET = {
    "low": 2_048,
    "medium": 6_000,
    "high": 12_000,
    "xhigh": 20_000,
    "max": 28_000,
    "ultra": 28_000,
}
THINKING_HEADROOM = 4_000
# Anthropic rejects a thinking budget below this, so a cap that cannot hold it gets no thinking at all.
ANTHROPIC_MIN_THINKING_BUDGET = 1_024
OPENAI_EFFORT = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
    "ultra": "ultra",
}
_OPENAI_STYLE_PROVIDERS = frozenset({"openai", "omniroute", "opencode", "nvidia"})


def normalize_effort(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return level if level in EFFORT_LEVELS else None


def reasoning_supported(provider: str, model_id: str, capabilities: Iterable[str]) -> bool:
    """Whether a reasoning control can be sent to this model without being rejected."""
    return bool(levels_for(provider, model_id, tuple(capabilities or ())).levels)


def apply_effort(
    provider: str,
    payload: MutableMapping[str, Any],
    effort: Any,
    *,
    explicit_cap: object = None,
    model_id: str = "",
    capabilities: Iterable[str] = (),
) -> MutableMapping[str, Any]:
    """Add the provider's reasoning control to a request payload (in place) and return it.

    ``explicit_cap`` is the output limit the caller asked for, when it asked for one. Effort never raises
    that limit: adaptive thinking leaves ``max_tokens`` untouched, and legacy ``budget_tokens`` is fitted
    inside it (omitted when it cannot fit). Never emits ``{"type": "disabled"}``.
    """
    level = normalize_effort(effort)
    if level is None:
        return payload

    name = str(provider or "").strip().lower()
    effective_model = str(model_id or payload.get("model") or "").strip()
    caps = tuple(capabilities or ())

    if name == "anthropic":
        info: ModelEffortInfo | None = (
            levels_for("anthropic", effective_model, caps) if effective_model else None
        )
        applied: str | None = level
        if info is not None:
            applied, _adj = clamp("anthropic", effective_model, level, caps, info=info)
            if applied is None:
                return payload
        if info is not None and info.adaptive_thinking:
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": applied}
            return payload
        budget = THINKING_BUDGET[applied]
        if isinstance(explicit_cap, int) and not isinstance(explicit_cap, bool) and explicit_cap > 0:
            fitted = min(budget, explicit_cap - ANTHROPIC_MIN_THINKING_BUDGET)
            if fitted >= ANTHROPIC_MIN_THINKING_BUDGET:
                payload["thinking"] = {"type": "enabled", "budget_tokens": fitted}
            return payload
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        current = payload.get("max_tokens")
        payload["max_tokens"] = max(
            current if isinstance(current, int) and not isinstance(current, bool) else 0,
            budget + THINKING_HEADROOM,
        )
        return payload

    if name == "openrouter":
        applied_or = level
        if effective_model or caps:
            info_or = levels_for("openrouter", effective_model, caps)
            if info_or.levels:
                clamped_or, _ = clamp("openrouter", effective_model, level, caps, info=info_or)
                applied_or = clamped_or or level
        payload["reasoning"] = {"effort": OPENAI_EFFORT.get(applied_or, applied_or)}
        return payload

    if name in _OPENAI_STYLE_PROVIDERS:
        applied_oai = level
        if effective_model or caps:
            info_oai = levels_for(name, effective_model, caps)
            if info_oai.levels:
                clamped_oai, _ = clamp(name, effective_model, level, caps, info=info_oai)
                applied_oai = clamped_oai or level
        payload["reasoning_effort"] = OPENAI_EFFORT.get(applied_oai, applied_oai)
        return payload

    return payload


__all__ = [
    "ANTHROPIC_MIN_THINKING_BUDGET",
    "EFFORT_LEVELS",
    "EffortAdjustment",
    "ModelEffortInfo",
    "OPENAI_EFFORT",
    "THINKING_BUDGET",
    "THINKING_HEADROOM",
    "_OPENAI_STYLE_PROVIDERS",
    "apply_effort",
    "clamp",
    "levels_for",
    "normalize_effort",
    "reasoning_supported",
]
