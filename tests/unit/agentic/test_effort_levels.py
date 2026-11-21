from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.effort import reasoning_supported
from agentos.agentic.effort_levels import EffortAdjustment, ModelEffortInfo, clamp, levels_for
from agentos.providers.claude_cli.catalog import CatalogModel, discover_cached_models
from agentos.providers.cli_adapter import CliModelInfo


def test_claude_opus_5_5_from_cache_returns_levels_and_default(tmp_path: Path) -> None:
    cache_dir = tmp_path / "model-catalog"
    cache_dir.mkdir(parents=True)
    (cache_dir / "org-cc.json").write_text(
        json.dumps(
            {
                "organizationUuid": "org-1",
                "fetchedAt": 1_700_000_000_000,
                "catalog": {
                    "surface": "cc",
                    "config": {
                        "models": [
                            {
                                "id": "claude-opus-5-5",
                                "name": "Opus 5.5",
                                "runtime": {
                                    "max_input_tokens": 1_000_000,
                                    "effort_levels": ["low", "medium", "high", "xhigh", "max"],
                                    "default_effort": "medium",
                                },
                            },
                            {
                                "id": "claude-fable-5-2",
                                "name": "Fable 5.2",
                                "runtime": {
                                    "max_input_tokens": 1_000_000,
                                    "effort_levels": ["low", "medium", "high", "xhigh", "max"],
                                    "default_effort": "medium",
                                    "rejects_disabled_thinking": True,
                                },
                            },
                            {
                                "id": "claude-opus-4-6",
                                "name": "Opus 4.6",
                                "runtime": {
                                    "max_input_tokens": 200_000,
                                    "effort_levels": ["low", "medium", "high", "max"],
                                    "default_effort": "medium",
                                },
                            },
                            {
                                "id": "claude-haiku-4-5-20251001",
                                "name": "Haiku 4.5",
                                "runtime": {
                                    "max_input_tokens": 200_000,
                                },
                            },
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    cached = discover_cached_models(cache_dir=cache_dir)
    by_id = {m.id: m for m in cached}
    assert by_id["claude-opus-5-5"].effort_levels == ("low", "medium", "high", "xhigh", "max")
    assert by_id["claude-opus-5-5"].default_effort == "medium"
    assert by_id["claude-opus-5-5"]["effort_levels"] == ("low", "medium", "high", "xhigh", "max")
    assert by_id["claude-fable-5-2"].rejects_disabled_thinking is True

    opus55 = levels_for("anthropic", "claude-opus-5-5", cached_claude_models=cached)
    assert isinstance(opus55, ModelEffortInfo)
    assert opus55.levels == ("low", "medium", "high", "xhigh", "max")
    assert opus55.default == "medium"
    assert opus55.adaptive_thinking is True
    assert opus55.legacy_thinking_budget is False
    assert opus55.source == "claude_cli_cache"

    fable52 = levels_for("anthropic", "claude-fable-5-2", cached_claude_models=cached)
    assert fable52.adaptive_thinking is True
    assert fable52.rejects_disabled_thinking is True

    opus46 = levels_for("anthropic", "claude-opus-4-6", cached_claude_models=cached)
    assert "xhigh" not in opus46.levels
    assert opus46.levels == ("low", "medium", "high", "max")
    assert opus46.legacy_thinking_budget is True


def test_claude_static_fallback_when_cache_missing() -> None:
    opus55 = levels_for("anthropic", "claude-opus-5-5", cached_claude_models=())
    assert opus55.levels == ("low", "medium", "high", "xhigh", "max")
    assert opus55.default == "medium"
    assert opus55.adaptive_thinking is True

    opus46 = levels_for("anthropic", "claude-opus-4-6", cached_claude_models=())
    assert "xhigh" not in opus46.levels
    assert opus46.legacy_thinking_budget is True

    haiku45 = levels_for("anthropic", "claude-haiku-4-5-20251001", cached_claude_models=())
    assert haiku45.levels == ()
    assert haiku45.default is None
    assert reasoning_supported("anthropic", "claude-haiku-4-5-20251001", ()) is False

    fable52 = levels_for("anthropic", "claude-fable-5-2", cached_claude_models=())
    assert fable52.adaptive_thinking is True
    assert fable52.rejects_disabled_thinking is True


def test_codex_clamp_without_max_or_ultra() -> None:
    codex_models = (
        CliModelInfo(
            id="gpt-6-compact",
            display_name="GPT-6 Compact",
            context_window=272_000,
            supported_efforts=("low", "medium", "high"),
            default_effort="medium",
        ),
        CliModelInfo(
            id="gpt-6-luna",
            display_name="GPT-6 Luna",
            context_window=272_000,
            supported_efforts=("low", "medium", "high", "xhigh"),
            default_effort="medium",
        ),
    )
    info_compact = levels_for("openai-codex", "gpt-6-compact", codex_models=codex_models)
    applied, adj = clamp("openai-codex", "gpt-6-compact", "max", info=info_compact)
    assert applied == "high"
    assert adj == EffortAdjustment(requested="max", applied="high", reason="unsupported_level")

    info_luna = levels_for("openai-codex", "gpt-6-luna", codex_models=codex_models)
    applied_luna, adj_luna = clamp("openai-codex", "gpt-6-luna", "max", info=info_luna)
    assert applied_luna == "xhigh"
    assert adj_luna == EffortAdjustment(requested="max", applied="xhigh", reason="unsupported_level")


def test_antigravity_slug_fixed_clamps_to_slug_level() -> None:
    info = levels_for("google-antigravity", "gemini-4-argon-high")
    assert info.levels == ("high",)
    assert info.default == "high"

    applied, adj = clamp("google-antigravity", "gemini-4-argon-high", "low")
    assert applied == "high"
    assert adj == EffortAdjustment(requested="low", applied="high", reason="model_slug_fixed")

    applied_same, adj_same = clamp("google-antigravity", "gemini-4-argon-high", "high")
    assert applied_same == "high"
    assert adj_same is None


def test_unknown_model_returns_empty_levels_without_error() -> None:
    info = levels_for("unknown-provider", "mystery-model-999")
    assert info.levels == ()
    assert info.default is None
    applied, adj = clamp("unknown-provider", "mystery-model-999", "high")
    assert applied is None
    assert adj == EffortAdjustment(requested="high", applied=None, reason="unsupported_model")


def test_catalog_model_dict_interface_preserved() -> None:
    cm = CatalogModel(
        id="claude-sonnet-5-5",
        name="Claude Sonnet 5.5 (Assinatura CLI)",
        context_window=1_000_000,
        effort_levels=("low", "medium", "high", "xhigh", "max"),
        default_effort="medium",
    )
    assert cm["id"] == "claude-sonnet-5-5"
    assert cm["context_window"] == 1_000_000
    assert cm["default_effort"] == "medium"
    assert cm.get("supported_efforts") == ("low", "medium", "high", "xhigh", "max")
