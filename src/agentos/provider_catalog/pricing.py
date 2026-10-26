"""USD cost estimation for conversation overview metrics."""
from __future__ import annotations

import math
from typing import Mapping


# Reference fallback rates (USD per 1M tokens: (input_per_million, output_per_million))
# Used when direct providers (OpenAI, Anthropic, NVIDIA, etc.) do not publish per-model pricing in /v1/models.
_FALLBACK_PRICING_PER_MILLION: tuple[tuple[str, tuple[float, float]], ...] = (
    ("claude-opus-4", (15.0, 75.0)),
    ("claude-3-opus", (15.0, 75.0)),
    ("claude-sonnet-4", (3.0, 15.0)),
    ("claude-3-7-sonnet", (3.0, 15.0)),
    ("claude-3-5-sonnet", (3.0, 15.0)),
    ("claude-haiku-4", (0.80, 4.0)),
    ("claude-3-5-haiku", (0.80, 4.0)),
    ("claude-3-haiku", (0.25, 1.25)),
    ("gpt-5-nano", (0.10, 0.40)),
    ("gpt-5-mini", (0.25, 2.0)),
    ("gpt-5", (1.25, 10.0)),
    ("gpt-4.1-nano", (0.10, 0.40)),
    ("gpt-4.1-mini", (0.40, 1.60)),
    ("gpt-4.1", (2.0, 8.0)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.50, 10.0)),
    ("o4-mini", (1.10, 4.40)),
    ("o3-mini", (1.10, 4.40)),
    ("o3", (2.0, 8.0)),
    ("o1-mini", (1.10, 4.40)),
    ("o1", (15.0, 60.0)),
    ("gemini-2.5-pro", (1.25, 10.0)),
    ("gemini-2.5-flash", (0.15, 0.60)),
    ("gemini-2.0-flash", (0.10, 0.40)),
    ("deepseek-reasoner", (0.55, 2.19)),
    ("deepseek-r1", (0.55, 2.19)),
    ("deepseek-chat", (0.27, 1.10)),
    ("deepseek-v3", (0.27, 1.10)),
    ("llama-3.1-405b", (5.0, 16.0)),
    ("llama-3.3-70b", (0.88, 0.88)),
    ("llama-3.1-70b", (0.88, 0.88)),
    ("llama-3.1-8b", (0.20, 0.20)),
    ("nemotron", (0.88, 0.88)),
    ("qwq-32b", (0.60, 0.60)),
)

_VENDOR_PREFIXES: tuple[str, ...] = (
    "meta/",
    "nvidia/",
    "deepseek-ai/",
    "mistralai/",
    "google/",
    "qwen/",
)


def _parse_rate(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = float(str(value).strip().replace("$", ""))
    except (ValueError, TypeError):
        return None
    if not math.isfinite(rate) or rate < 0:
        return None
    return rate


def _match_pricing(model_id: str) -> tuple[float, float] | None:
    norm_model = str(model_id or "").strip().lower().lstrip("~")
    if not norm_model:
        return None
    stripped = norm_model
    for vendor in _VENDOR_PREFIXES:
        if stripped.startswith(vendor):
            stripped = stripped[len(vendor) :]
            break
    slug = stripped.split("/", 1)[-1]
    for prefix, rates in _FALLBACK_PRICING_PER_MILLION:
        if stripped.startswith(prefix) or slug.startswith(prefix) or prefix in norm_model:
            return rates
    return None


def resolve_rates_per_million(
    provider: str,
    model_id: str,
    catalog_pricing: Mapping[str, object] | None = None,
) -> tuple[float, float] | None:
    norm_provider = str(provider or "").strip().lower()
    if norm_provider == "ollama":
        return (0.0, 0.0)

    if isinstance(catalog_pricing, Mapping):
        in_rate = _parse_rate(
            catalog_pricing.get("input_per_million")
            or catalog_pricing.get("prompt_per_million")
            or catalog_pricing.get("input")
        )
        out_rate = _parse_rate(
            catalog_pricing.get("output_per_million")
            or catalog_pricing.get("completion_per_million")
            or catalog_pricing.get("output")
        )
        if in_rate is not None and out_rate is not None:
            return (in_rate, out_rate)

    return _match_pricing(model_id)


def estimate_cost_usd(
    *,
    provider: str,
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    catalog_pricing: Mapping[str, object] | None = None,
) -> float | None:
    if input_tokens is None and output_tokens is None:
        return None
    rates = resolve_rates_per_million(provider, model_id, catalog_pricing)
    if rates is None:
        return None
    in_tokens = max(0, int(input_tokens or 0))
    out_tokens = max(0, int(output_tokens or 0))
    in_rate, out_rate = rates
    total = (in_tokens / 1_000_000.0) * in_rate + (out_tokens / 1_000_000.0) * out_rate
    return round(total, 6)


__all__ = ["_match_pricing", "estimate_cost_usd", "resolve_rates_per_million"]
