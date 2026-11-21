"""Model family and capability tier catalog for verifier selection (WS-A2, spec 5.3).

Critical invariant:
    A model's family is determined by the **model**, never by the CLI/provider
    hosting it. For example, ``claude-opus-5-5-high`` running on
    ``google-antigravity`` belongs to family ``"anthropic"``, not ``"google"``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

# Ordered longest-first so ``-xhigh`` is stripped before ``-high``.
EFFORT_SUFFIXES: tuple[str, ...] = (
    "-xhigh",
    "-medium",
    "-ultra",
    "-high",
    "-low",
    "-max",
)

_OPENAI_O_RE = re.compile(r"^o[1-9](?:[-.]|$)")

# Canonical initial table from spec section 5.3.
# Values use ``{"family": str, "tier": int}`` so users can edit both family and
# tier in ``orchestration.modelTiers``.
DEFAULT_MODEL_TIERS: dict[str, dict[str, Any]] = {
    # anthropic
    "claude-fable-5-2": {"family": "anthropic", "tier": 5},
    "claude-fable-5-1": {"family": "anthropic", "tier": 5},
    "claude-opus-5-5": {"family": "anthropic", "tier": 4},
    "claude-sonnet-5-5": {"family": "anthropic", "tier": 3},
    "claude-haiku-5-5": {"family": "anthropic", "tier": 2},
    # openai
    "gpt-6.1-sol": {"family": "openai", "tier": 4},
    "gpt-6.1-terra": {"family": "openai", "tier": 4},
    "gpt-6-sol": {"family": "openai", "tier": 4},
    "gpt-6-astra": {"family": "openai", "tier": 3},
    "gpt-5.6-sol": {"family": "openai", "tier": 3},
    "gpt-5.6-terra": {"family": "openai", "tier": 3},
    "gpt-6-luna": {"family": "openai", "tier": 2},
    "gpt-5.6-luna": {"family": "openai", "tier": 2},
    # openai-oss
    "gpt-oss-120b": {"family": "openai-oss", "tier": 1},
    # google
    "gemini-4-hydrogen": {"family": "google", "tier": 4},
    "gemini-4-argon": {"family": "google", "tier": 4},
    "gemini-4-carbon": {"family": "google", "tier": 3},
    "gemini-3.8-flash": {"family": "google", "tier": 2},
}


def default_model_tiers_copy() -> dict[str, dict[str, Any]]:
    """Return a deep copy of :data:`DEFAULT_MODEL_TIERS`."""
    return copy.deepcopy(DEFAULT_MODEL_TIERS)


def normalize_model_id(provider: str, model: str) -> str:
    """Normalize a model identifier by stripping provider/org prefixes and ``~``."""
    raw = str(model or "").strip().lower().lstrip("~")
    if not raw:
        return ""
    prov = str(provider or "").strip().lower()
    if prov and raw.startswith(f"{prov}/"):
        raw = raw[len(prov) + 1 :]
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    return raw.strip()


def strip_effort_suffix(model_id: str) -> str:
    """Strip a trailing effort suffix (e.g. ``-high``, ``-xhigh``) from a normalized model id."""
    cleaned = str(model_id or "").strip().lower()
    for suffix in EFFORT_SUFFIXES:
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
            return cleaned[: -len(suffix)]
    return cleaned


def infer_family_from_model(model: str) -> str:
    """Infer the model family strictly from the model name (never from the CLI provider)."""
    clean = normalize_model_id("", model)
    if not clean:
        return "other"
    if clean.startswith("claude-"):
        return "anthropic"
    if clean.startswith("gpt-oss"):
        return "openai-oss"
    if clean.startswith(("gpt-", "codex-")) or _OPENAI_O_RE.match(clean):
        return "openai"
    if clean.startswith(("gemini-", "gemma-")):
        return "google"
    return "other"


def _parse_tier_entry(
    raw_key: str,
    raw_value: Any,
    existing: tuple[str, int] | None = None,
) -> tuple[str, int] | None:
    clean_key = normalize_model_id("", raw_key.rstrip("*").rstrip("-"))
    fallback_family = existing[0] if existing is not None else infer_family_from_model(clean_key)

    if isinstance(raw_value, int) and not isinstance(raw_value, bool):
        if raw_value < 0:
            return None
        return (fallback_family, int(raw_value))

    if isinstance(raw_value, Mapping):
        raw_tier = raw_value.get("tier")
        if isinstance(raw_tier, bool) or not isinstance(raw_tier, int) or raw_tier < 0:
            return None
        raw_family = raw_value.get("family")
        if isinstance(raw_family, str) and raw_family.strip():
            family = raw_family.strip().lower()
        else:
            family = fallback_family
        return (family, int(raw_tier))

    return None


def _build_effective_tables(
    tiers: Mapping[str, Any] | None,
) -> tuple[dict[str, tuple[str, int]], list[tuple[str, str, int]]]:
    exact: dict[str, tuple[str, int]] = {
        k: (str(v["family"]).strip().lower(), int(v["tier"]))
        for k, v in DEFAULT_MODEL_TIERS.items()
    }
    wildcards: dict[str, tuple[str, int]] = {}

    if isinstance(tiers, Mapping):
        for raw_key, raw_value in tiers.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                continue
            key_norm = raw_key.strip().lower().lstrip("~")
            if key_norm.endswith("*"):
                prefix = key_norm[:-1]
                if not prefix:
                    continue
                base_lookup = prefix.rstrip("-")
                existing = exact.get(base_lookup)
                parsed = _parse_tier_entry(key_norm, raw_value, existing=existing)
                if parsed is not None:
                    wildcards[prefix] = parsed
                continue

            clean_key = normalize_model_id("", key_norm)
            existing = exact.get(key_norm) or exact.get(clean_key)
            parsed = _parse_tier_entry(key_norm, raw_value, existing=existing)
            if parsed is not None:
                exact[key_norm] = parsed

    wildcard_list = sorted(
        ((prefix, fam, tier) for prefix, (fam, tier) in wildcards.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    return exact, wildcard_list


def _resolve_model_entry(
    provider: str,
    model: str,
    tiers: Mapping[str, Any] | None = None,
) -> tuple[str, int]:
    raw_lower = str(model or "").strip().lower().lstrip("~")
    if not raw_lower:
        return ("other", 0)

    prov_norm = str(provider or "").strip().lower()
    clean = normalize_model_id(prov_norm, raw_lower)
    if not clean:
        return ("other", 0)

    exact, wildcards = _build_effective_tables(tiers)

    # 1. Exact matches (qualified, raw, normalized) take priority — including explicit tier=0 overrides.
    qualified = f"{prov_norm}/{clean}" if prov_norm else ""
    for candidate_key in (qualified, raw_lower, clean):
        if candidate_key and candidate_key in exact:
            return exact[candidate_key]

    # 2. Wildcard override entries (e.g. "gemini-4-neon-*").
    for prefix, fam, tier in wildcards:
        if raw_lower.startswith(prefix) or clean.startswith(prefix):
            return (fam, tier)

    # 3. Exact base match after stripping a trailing effort suffix (e.g. "-high", "-xhigh").
    base = strip_effort_suffix(clean)
    if base != clean and base in exact:
        return exact[base]

    # 4. Sibling inheritance: same family and same base name (either sibling with
    #    effort suffix, or exact base entry followed by "-" tag/date/variant).
    inferred_family = infer_family_from_model(clean)
    for entry_key, (entry_fam, entry_tier) in exact.items():
        if "/" in entry_key:
            continue
        if entry_fam != inferred_family:
            continue
        if strip_effort_suffix(entry_key) == base:
            return (entry_fam, entry_tier)

    prefix_match: tuple[int, str, int] | None = None
    for entry_key, (entry_fam, entry_tier) in exact.items():
        if "/" in entry_key:
            continue
        if entry_fam != inferred_family:
            continue
        if clean.startswith(f"{entry_key}-"):
            if prefix_match is None or len(entry_key) > prefix_match[0]:
                prefix_match = (len(entry_key), entry_fam, entry_tier)
    if prefix_match is not None:
        return (prefix_match[1], prefix_match[2])

    return (inferred_family, 0)


def family_of(
    provider: str,
    model: str,
    tiers: Mapping[str, Any] | None = None,
) -> str:
    """Return the model family (determined from the model, never from the CLI)."""
    family, _ = _resolve_model_entry(provider, model, tiers=tiers)
    return family


def tier_of(
    provider: str,
    model: str,
    tiers: Mapping[str, Any] | None = None,
) -> int:
    """Return the capability tier (0 for unknown models without a known sibling)."""
    _, tier = _resolve_model_entry(provider, model, tiers=tiers)
    return tier


__all__ = [
    "DEFAULT_MODEL_TIERS",
    "EFFORT_SUFFIXES",
    "default_model_tiers_copy",
    "family_of",
    "infer_family_from_model",
    "normalize_model_id",
    "strip_effort_suffix",
    "tier_of",
]
