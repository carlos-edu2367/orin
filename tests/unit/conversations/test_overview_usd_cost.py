from __future__ import annotations

import pytest

from agentos.conversations.chat import _token_usage, _total_token_usage, _unavailable_token_usage
from agentos.provider_catalog.pricing import estimate_cost_usd


def test_estimate_cost_usd_uses_catalog_rates_and_fallback_table() -> None:
    # 1. Explicit catalog rates (e.g., OpenRouter / OmniRoute pricing per 1M tokens)
    cost = estimate_cost_usd(
        provider="openrouter",
        model_id="custom/model",
        input_tokens=500_000,
        output_tokens=100_000,
        catalog_pricing={"input_per_million": "2.00", "output_per_million": "8.00"},
    )
    assert cost == pytest.approx(1.80)

    # 2. Fallback reference table for direct providers (OpenAI / Anthropic) without catalog pricing
    sonnet_cost = estimate_cost_usd(
        provider="anthropic",
        model_id="claude-sonnet-4-5-20250929",
        input_tokens=100_000,
        output_tokens=20_000,
        catalog_pricing=None,
    )
    assert sonnet_cost is not None and sonnet_cost > 0

    # 3. Ollama local models are always $0.00
    ollama_cost = estimate_cost_usd(
        provider="ollama",
        model_id="qwen2.5:14b",
        input_tokens=50_000,
        output_tokens=10_000,
        catalog_pricing=None,
    )
    assert ollama_cost == 0.0

    # 4. Unknown custom model with no catalog pricing returns None (honest unavailability)
    unknown = estimate_cost_usd(
        provider="openai",
        model_id="totally-unknown-custom-model",
        input_tokens=10_000,
        output_tokens=5_000,
        catalog_pricing=None,
    )
    assert unknown is None


def test_estimate_cost_usd_adversarial_malformed_rates_and_negative_tokens() -> None:
    assert (
        estimate_cost_usd(
            provider="openrouter",
            model_id="bad/rate",
            input_tokens=1000,
            output_tokens=1000,
            catalog_pricing={"input_per_million": "NaN", "output_per_million": "not-a-number"},
        )
        is None
    )
    assert (
        estimate_cost_usd(
            provider="openai",
            model_id="gpt-4o",
            input_tokens=-500,
            output_tokens=-100,
            catalog_pricing=None,
        )
        == 0.0
    )


def test_token_usage_helpers_include_estimated_cost_usd() -> None:
    unavail = _unavailable_token_usage()
    assert unavail["estimated_cost_usd"] is None

    u1 = _token_usage(
        {
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "total_tokens": 2_000_000,
            "usage_reported": True,
        }
    )
    assert u1["estimated_cost_usd"] is not None and u1["estimated_cost_usd"] > 0

    u2 = _token_usage(
        {
            "provider": "ollama",
            "model_id": "llama3",
            "input_tokens": 5_000,
            "output_tokens": 1_000,
            "total_tokens": 6_000,
            "usage_reported": True,
        }
    )
    assert u2["estimated_cost_usd"] == 0.0

    total = _total_token_usage([u1, u2])
    assert total["estimated_cost_usd"] == pytest.approx(u1["estimated_cost_usd"])
