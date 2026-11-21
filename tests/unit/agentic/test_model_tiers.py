"""Unit tests for model family and tier resolution (WS-A2, spec 5.3)."""

from __future__ import annotations

import pytest

from agentos.agentic.model_tiers import (
    DEFAULT_MODEL_TIERS,
    family_of,
    tier_of,
)


def test_default_table_covers_spec_section_5_3_models_and_tiers() -> None:
    expected = [
        # anthropic
        ("anthropic-cli", "claude-fable-5-2", "anthropic", 5),
        ("anthropic-cli", "claude-fable-5-1", "anthropic", 5),
        ("anthropic-cli", "claude-opus-5-5", "anthropic", 4),
        ("google-antigravity", "claude-opus-5-5-low", "anthropic", 4),
        ("google-antigravity", "claude-opus-5-5-medium", "anthropic", 4),
        ("google-antigravity", "claude-opus-5-5-high", "anthropic", 4),
        ("anthropic-cli", "claude-sonnet-5-5", "anthropic", 3),
        ("google-antigravity", "claude-sonnet-5-5-low", "anthropic", 3),
        ("google-antigravity", "claude-sonnet-5-5-medium", "anthropic", 3),
        ("google-antigravity", "claude-sonnet-5-5-high", "anthropic", 3),
        ("anthropic-cli", "claude-haiku-5-5", "anthropic", 2),
        # openai
        ("openai-codex", "gpt-6.1-sol", "openai", 4),
        ("openai-codex", "gpt-6.1-terra", "openai", 4),
        ("openai-codex", "gpt-6-sol", "openai", 4),
        ("openai-codex", "gpt-6-astra", "openai", 3),
        ("openai-codex", "gpt-5.6-sol", "openai", 3),
        ("openai-codex", "gpt-5.6-terra", "openai", 3),
        ("openai-codex", "gpt-6-luna", "openai", 2),
        ("openai-codex", "gpt-5.6-luna", "openai", 2),
        # openai-oss
        ("google-antigravity", "gpt-oss-120b-medium", "openai-oss", 1),
        ("google-antigravity", "gpt-oss-120b-high", "openai-oss", 1),
        # google
        ("google-antigravity", "gemini-4-hydrogen-high", "google", 4),
        ("google-antigravity", "gemini-4-hydrogen-xhigh", "google", 4),
        ("google-antigravity", "gemini-4-argon-high", "google", 4),
        ("google-antigravity", "gemini-4-argon-medium", "google", 4),
        ("google-antigravity", "gemini-4-carbon-high", "google", 3),
        ("google-antigravity", "gemini-4-carbon-low", "google", 3),
        ("google-antigravity", "gemini-3.8-flash-high", "google", 2),
        ("google-antigravity", "gemini-3.8-flash-medium", "google", 2),
    ]
    for provider, model, expected_family, expected_tier in expected:
        assert family_of(provider, model) == expected_family, (provider, model)
        assert tier_of(provider, model) == expected_tier, (provider, model)


def test_family_comes_from_model_never_from_cli_provider() -> None:
    # Claude running on Antigravity is Anthropic, NOT Google
    assert family_of("google-antigravity", "claude-opus-5-5-high") == "anthropic"
    assert family_of("google-antigravity", "claude-sonnet-5-5-medium") == "anthropic"
    # OpenRouter-prefixed Claude is still Anthropic
    assert family_of("openrouter", "anthropic/claude-opus-5-5") == "anthropic"
    assert tier_of("openrouter", "anthropic/claude-opus-5-5") == 4
    # GPT-OSS running on Antigravity is openai-oss, NOT google and NOT openai
    assert family_of("google-antigravity", "gpt-oss-120b-medium") == "openai-oss"
    # Provider prefix in model id is stripped cleanly
    assert family_of("google-antigravity", "google-antigravity/claude-opus-5-5-high") == "anthropic"
    assert tier_of("openai-codex", "openai-codex/gpt-6.1-sol") == 4


def test_sibling_inheritance_for_same_family_and_base_name() -> None:
    # New effort suffixes inherit from base sibling
    assert tier_of("google-antigravity", "claude-sonnet-5-5-xhigh") == 3
    assert tier_of("google-antigravity", "gemini-4-argon-xhigh") == 4
    assert tier_of("google-antigravity", "gemini-4-carbon-xhigh") == 3
    assert tier_of("google-antigravity", "gpt-oss-120b-ultra") == 1
    # Date / preview suffix sharing exact base name inherits
    assert tier_of("anthropic-cli", "claude-sonnet-5-5-20261101") == 3
    assert tier_of("openai-codex", "gpt-6.1-sol-preview") == 4
    # Sibling defined in custom table propagates to another effort variant of same base
    custom = {"gemini-4-neon-high": {"family": "google", "tier": 4}}
    assert family_of("google-antigravity", "gemini-4-neon-xhigh", tiers=custom) == "google"
    assert tier_of("google-antigravity", "gemini-4-neon-xhigh", tiers=custom) == 4


def test_unknown_models_without_sibling_have_tier_zero() -> None:
    assert tier_of("openrouter", "acme/unknown-model-v1") == 0
    assert family_of("openrouter", "acme/unknown-model-v1") == "other"
    # Older generations without explicit table entry are NOT siblings of newer major versions
    assert tier_of("anthropic-cli", "claude-opus-4-6") == 0
    assert family_of("anthropic-cli", "claude-opus-4-6") == "anthropic"
    assert tier_of("openai", "gpt-4o") == 0
    assert family_of("openai", "gpt-4o") == "openai"
    assert tier_of("google-antigravity", "gemini-2.5-pro") == 0
    assert family_of("google-antigravity", "gemini-2.5-pro") == "google"


def test_editable_tiers_override_both_int_and_object_formats() -> None:
    # Simple {model: tier} format
    int_overrides = {
        "claude-opus-5-5": 5,
        "custom-reviewer": 4,
    }
    assert tier_of("anthropic-cli", "claude-opus-5-5", tiers=int_overrides) == 5
    assert tier_of("google-antigravity", "claude-opus-5-5-high", tiers=int_overrides) == 5
    assert family_of("anthropic-cli", "claude-opus-5-5", tiers=int_overrides) == "anthropic"
    assert tier_of("openrouter", "custom-reviewer", tiers=int_overrides) == 4
    # Unmodified default entries remain intact
    assert tier_of("openai-codex", "gpt-6.1-sol", tiers=int_overrides) == 4

    # Canonical {model: {"family": ..., "tier": ...}} format
    dict_overrides = {
        "acme-pro-1": {"family": "acme", "tier": 4},
        "claude-opus-5-5-high": {"family": "anthropic", "tier": 0},
    }
    assert family_of("openrouter", "acme-pro-1", tiers=dict_overrides) == "acme"
    assert tier_of("openrouter", "acme-pro-1", tiers=dict_overrides) == 4
    # Exact variant override to 0 must NOT fall back to base sibling tier 4
    assert tier_of("google-antigravity", "claude-opus-5-5-high", tiers=dict_overrides) == 0
    assert tier_of("google-antigravity", "claude-opus-5-5-low", tiers=dict_overrides) == 4


def test_default_model_tiers_is_canonical_dict_structure() -> None:
    assert "claude-opus-5-5" in DEFAULT_MODEL_TIERS
    entry = DEFAULT_MODEL_TIERS["claude-opus-5-5"]
    assert entry == {"family": "anthropic", "tier": 4}
    for model_id, spec in DEFAULT_MODEL_TIERS.items():
        assert isinstance(model_id, str) and model_id
        assert isinstance(spec, dict)
        assert isinstance(spec["family"], str) and spec["family"]
        assert isinstance(spec["tier"], int) and 1 <= spec["tier"] <= 5


@pytest.mark.parametrize("pattern_key", ["gemini-4-neon-*", "gpt-oss-120b-*"])
def test_wildcard_override_entries_are_matched(pattern_key: str) -> None:
    custom = {pattern_key: {"family": "customfam", "tier": 5}}
    sample = pattern_key.replace("-*", "-high")
    assert family_of("google-antigravity", sample, tiers=custom) == "customfam"
    assert tier_of("google-antigravity", sample, tiers=custom) == 5
