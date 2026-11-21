"""Catalog of supported reasoning effort levels and clamping per provider/model (WS-E3)."""
from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
import re
from typing import Any

EFFORT_ORDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max", "ultra")
EFFORT_RANK: dict[str, int] = {level: idx for idx, level in enumerate(EFFORT_ORDER)}

_CLAUDE_5_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_CLAUDE_LEGACY_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")
_OPENAI_5_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh")
_OPENAI_O_LEVELS: tuple[str, ...] = ("low", "medium", "high")
_OPENROUTER_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

_CLAUDE_5_RE = re.compile(r"^claude-(?:opus|sonnet|haiku|fable)-(?:[5-9])(?:-[0-9]+)?")
_CLAUDE_4_7_OPUS_RE = re.compile(r"^claude-opus-4-7\b")
_CLAUDE_HAIKU_4_RE = re.compile(r"^claude-haiku-4(?:-|$)")
_CLAUDE_LEGACY_THINKING_RE = re.compile(r"^claude-(?:(?:opus|sonnet)-4|4|3-7)")
_OPENAI_GPT_REASONING_RE = re.compile(r"^gpt-[5-9]")
_OPENAI_O_REASONING_RE = re.compile(r"^o[1-9]")
_AGY_SUFFIXES: tuple[str, ...] = ("xhigh", "high", "medium", "low", "max")


@dataclass(frozen=True, slots=True)
class ModelEffortInfo:
    levels: tuple[str, ...]
    default: str | None
    source: str
    adaptive_thinking: bool = False
    legacy_thinking_budget: bool = False
    rejects_disabled_thinking: bool = False


@dataclass(frozen=True, slots=True)
class EffortAdjustment:
    requested: str
    applied: str | None
    reason: str


def _normalize_model(provider: str, model_id: str) -> str:
    raw = str(model_id or "").strip().lower().lstrip("~")
    if raw.startswith(f"{provider}/"):
        raw = raw[len(provider) + 1 :]
    elif "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    return raw.strip()


def _default_for(levels: tuple[str, ...]) -> str | None:
    if not levels:
        return None
    if len(levels) == 1:
        return levels[0]
    return "medium" if "medium" in levels else levels[0]


def _static_anthropic_info(model: str, cap_names: set[str]) -> ModelEffortInfo:
    if _CLAUDE_HAIKU_4_RE.match(model):
        return ModelEffortInfo(levels=(), default=None, source="static_table")
    if _CLAUDE_5_RE.match(model):
        rejects_disabled = model.startswith("claude-fable-5-2")
        return ModelEffortInfo(
            levels=_CLAUDE_5_LEVELS,
            default="medium",
            source="static_table",
            adaptive_thinking=True,
            legacy_thinking_budget=False,
            rejects_disabled_thinking=rejects_disabled,
        )
    if _CLAUDE_4_7_OPUS_RE.match(model):
        return ModelEffortInfo(
            levels=_CLAUDE_5_LEVELS,
            default="medium",
            source="static_table",
            adaptive_thinking=False,
            legacy_thinking_budget=True,
        )
    if _CLAUDE_LEGACY_THINKING_RE.match(model):
        return ModelEffortInfo(
            levels=_CLAUDE_LEGACY_LEVELS,
            default="medium",
            source="static_table",
            adaptive_thinking=False,
            legacy_thinking_budget=True,
        )
    if any("thinking" in item for item in cap_names) or "reasoning" in cap_names:
        return ModelEffortInfo(
            levels=_CLAUDE_LEGACY_LEVELS,
            default="medium",
            source="capabilities",
            adaptive_thinking=False,
            legacy_thinking_budget=True,
        )
    return ModelEffortInfo(levels=(), default=None, source="unknown")


def _lookup_claude_cached(
    model: str,
    cached_claude_models: Sequence[Any] | None,
) -> Any | None:
    entries: Sequence[Any]
    if cached_claude_models is not None:
        entries = cached_claude_models
    else:
        try:
            from agentos.providers.claude_cli.catalog import discover_cached_models

            entries = discover_cached_models()
        except Exception:
            return None
    for entry in entries:
        entry_id = str(getattr(entry, "id", None) or (entry.get("id") if isinstance(entry, dict) else "") or "").strip().lower()
        if entry_id == model:
            return entry
    return None


def _anthropic_levels_for(
    model: str,
    cap_names: set[str],
    cached_claude_models: Sequence[Any] | None,
) -> ModelEffortInfo:
    static = _static_anthropic_info(model, cap_names)
    cached = _lookup_claude_cached(model, cached_claude_models)
    if cached is not None:
        has_meta = bool(getattr(cached, "has_effort_metadata", False))
        raw_levels = tuple(getattr(cached, "effort_levels", ()) or ())
        raw_default = getattr(cached, "default_effort", None)
        rejects_disabled = bool(getattr(cached, "rejects_disabled_thinking", False)) or static.rejects_disabled_thinking
        if has_meta or raw_levels:
            levels = tuple(lvl for lvl in raw_levels if lvl in EFFORT_RANK)
            default = raw_default if raw_default in levels else _default_for(levels)
            return ModelEffortInfo(
                levels=levels,
                default=default,
                source="claude_cli_cache",
                adaptive_thinking=static.adaptive_thinking,
                legacy_thinking_budget=static.legacy_thinking_budget,
                rejects_disabled_thinking=rejects_disabled,
            )
        if rejects_disabled != static.rejects_disabled_thinking:
            return ModelEffortInfo(
                levels=static.levels,
                default=static.default,
                source="claude_cli_cache",
                adaptive_thinking=static.adaptive_thinking,
                legacy_thinking_budget=static.legacy_thinking_budget,
                rejects_disabled_thinking=rejects_disabled,
            )
    return static


def _codex_levels_for(
    model: str,
    cap_names: set[str],
    codex_models: Sequence[Any] | None,
) -> ModelEffortInfo:
    models: Sequence[Any]
    if codex_models is not None:
        models = codex_models
    else:
        try:
            from agentos.providers.codex_cli.catalog import discover_codex_models

            models = discover_codex_models()
        except Exception:
            models = ()
    for info in models:
        info_id = str(getattr(info, "id", "") or "").strip().lower()
        if info_id == model:
            levels = tuple(
                lvl
                for lvl in (str(e).strip().lower() for e in (getattr(info, "supported_efforts", ()) or ()))
                if lvl in EFFORT_RANK
            )
            raw_default = getattr(info, "default_effort", None)
            default = raw_default if raw_default in levels else _default_for(levels)
            return ModelEffortInfo(levels=levels, default=default, source="codex_catalog")
    if "reasoning" in cap_names or _OPENAI_GPT_REASONING_RE.match(model) or _OPENAI_O_REASONING_RE.match(model):
        return ModelEffortInfo(levels=_OPENAI_5_LEVELS, default="medium", source="capabilities")
    return ModelEffortInfo(levels=(), default=None, source="unknown")


def _antigravity_levels_for(
    model: str,
    cap_names: set[str],
    antigravity_models: Sequence[Any] | None,
) -> ModelEffortInfo:
    if model == "antigravity-image":
        return ModelEffortInfo(levels=(), default=None, source="antigravity_catalog")
    for suffix in _AGY_SUFFIXES:
        if model.endswith(f"-{suffix}"):
            return ModelEffortInfo(levels=(suffix,), default=suffix, source="antigravity_slug")
    models: Sequence[Any]
    if antigravity_models is not None:
        models = antigravity_models
    else:
        try:
            from agentos.providers.antigravity_cli.catalog import FALLBACK_ANTIGRAVITY_MODELS

            models = FALLBACK_ANTIGRAVITY_MODELS
        except Exception:
            models = ()
    for info in models:
        info_id = str(getattr(info, "id", "") or "").strip().lower()
        if info_id == model:
            levels = tuple(
                lvl
                for lvl in (str(e).strip().lower() for e in (getattr(info, "supported_efforts", ()) or ()))
                if lvl in EFFORT_RANK
            )
            raw_default = getattr(info, "default_effort", None)
            default = raw_default if raw_default in levels else _default_for(levels)
            return ModelEffortInfo(levels=levels, default=default, source="antigravity_catalog")
    if (
        "reasoning" in cap_names
        or any("thinking" in item for item in cap_names)
        or model.startswith(("gemini-", "claude-"))
        or bool(_OPENAI_GPT_REASONING_RE.match(model))
        or bool(_OPENAI_O_REASONING_RE.match(model))
    ):
        return ModelEffortInfo(levels=_OPENAI_O_LEVELS, default="medium", source="antigravity_inferred")
    return ModelEffortInfo(levels=(), default=None, source="unknown")


def levels_for(
    provider: str,
    model_id: str,
    capabilities: Collection[str] = (),
    *,
    cached_claude_models: Sequence[Any] | None = None,
    codex_models: Sequence[Any] | None = None,
    antigravity_models: Sequence[Any] | None = None,
) -> ModelEffortInfo:
    """Return supported effort levels, default level, and thinking protocol flags for a model."""
    name = str(provider or "").strip().lower()
    model = _normalize_model(name, model_id)
    cap_names = {str(item).strip().lower() for item in (capabilities or ())}
    if not name or not model:
        return ModelEffortInfo(levels=(), default=None, source="unknown")

    if name in {"anthropic", "anthropic-cli", "claude-cli", "claude"}:
        return _anthropic_levels_for(model, cap_names, cached_claude_models)

    if name == "openai-codex":
        return _codex_levels_for(model, cap_names, codex_models)

    if name == "google-antigravity":
        return _antigravity_levels_for(model, cap_names, antigravity_models)

    if name == "openai":
        if _OPENAI_GPT_REASONING_RE.match(model):
            return ModelEffortInfo(levels=_OPENAI_5_LEVELS, default="medium", source="static_table")
        if _OPENAI_O_REASONING_RE.match(model):
            return ModelEffortInfo(levels=_OPENAI_O_LEVELS, default="medium", source="static_table")
        if "reasoning" in cap_names:
            return ModelEffortInfo(levels=_OPENAI_O_LEVELS, default="medium", source="capabilities")
        return ModelEffortInfo(levels=(), default=None, source="unknown")

    if name in {"openrouter", "opencode"}:
        if "reasoning" in cap_names:
            return ModelEffortInfo(levels=_OPENROUTER_LEVELS, default="medium", source="capabilities")
        if name == "opencode" and (_OPENAI_GPT_REASONING_RE.match(model) or _OPENAI_O_REASONING_RE.match(model)):
            return ModelEffortInfo(levels=_OPENAI_5_LEVELS, default="medium", source="static_table")
        return ModelEffortInfo(levels=(), default=None, source="unknown")

    if name in {"omniroute", "nvidia"}:
        if "reasoning" in cap_names:
            return ModelEffortInfo(levels=_OPENAI_O_LEVELS, default="medium", source="capabilities")
        return ModelEffortInfo(levels=(), default=None, source="unknown")

    return ModelEffortInfo(levels=(), default=None, source="unknown")


def clamp(
    provider: str,
    model_id: str,
    requested: str | None,
    capabilities: Collection[str] = (),
    *,
    info: ModelEffortInfo | None = None,
) -> tuple[str | None, EffortAdjustment | None]:
    """Clamp ``requested`` to the highest effort level accepted by the model without exceeding it."""
    if requested is None:
        return None, None
    req = str(requested).strip().lower()
    if req not in EFFORT_RANK:
        return None, None

    model_info = info if info is not None else levels_for(provider, model_id, capabilities)
    if not model_info.levels:
        return None, EffortAdjustment(requested=req, applied=None, reason="unsupported_model")
    if req in model_info.levels:
        return req, None
    if model_info.source == "antigravity_slug" and len(model_info.levels) == 1:
        fixed = model_info.levels[0]
        return fixed, EffortAdjustment(requested=req, applied=fixed, reason="model_slug_fixed")

    req_rank = EFFORT_RANK[req]
    ordered = sorted(
        (lvl for lvl in dict.fromkeys(model_info.levels) if lvl in EFFORT_RANK),
        key=lambda lvl: EFFORT_RANK[lvl],
    )
    lower_or_eq = [lvl for lvl in ordered if EFFORT_RANK[lvl] <= req_rank]
    applied = lower_or_eq[-1] if lower_or_eq else (ordered[0] if ordered else None)
    return applied, EffortAdjustment(requested=req, applied=applied, reason="unsupported_level")


__all__ = [
    "EFFORT_ORDER",
    "EFFORT_RANK",
    "EffortAdjustment",
    "ModelEffortInfo",
    "clamp",
    "levels_for",
]
