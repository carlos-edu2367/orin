from __future__ import annotations

import json
from pathlib import Path

from agentos.providers.cli_adapter import CliModelInfo
from agentos.providers.codex_cli.catalog import (
    DEFAULT_CODEX_CONTEXT_WINDOW,
    FALLBACK_CODEX_MODELS,
    context_window_for,
    discover_codex_models,
    resolve_codex_home,
)


def _sample_models_cache_payload() -> dict[str, object]:
    def _levels(*efforts: str) -> list[dict[str, str]]:
        return [{"effort": e, "description": f"{e} effort"} for e in efforts]

    all_six = _levels("low", "medium", "high", "xhigh", "max", "ultra")
    five_no_ultra = _levels("low", "medium", "high", "xhigh", "max")

    return {
        "fetched_at": "2026-11-06T00:00:00Z",
        "models": [
            {
                "slug": "gpt-6.1-sol",
                "display_name": "GPT-6.1-Sol",
                "visibility": "list",
                "context_window": 500_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "gpt-6-astra",
                "display_name": "GPT-6-Astra",
                "visibility": "list",
                "context_window": 272_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "gpt-6.1-terra",
                "display_name": "GPT-6.1 Terra",
                "visibility": "list",
                "context_window": 500_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "gpt-6-sol",
                "display_name": "GPT-6-Sol",
                "visibility": "list",
                "context_window": 272_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "gpt-6-luna",
                "display_name": "GPT-6-Luna",
                "visibility": "list",
                "context_window": 272_000,
                "supported_reasoning_levels": five_no_ultra,
            },
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6-Sol",
                "visibility": "list",
                "context_window": 272_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "gpt-5.6-terra",
                "display_name": "GPT-5.6-Terra",
                "visibility": "list",
                "context_window": 272_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "gpt-5.6-luna",
                "display_name": "GPT-5.6-Luna",
                "visibility": "list",
                "context_window": 272_000,
                "supported_reasoning_levels": five_no_ultra,
            },
            {
                "slug": "gpt-reserve",
                "display_name": "GPT-Reserve",
                "visibility": "hide",
                "context_window": 272_000,
                "supported_reasoning_levels": all_six,
            },
            {
                "slug": "codex-auto-review",
                "display_name": "Codex Auto Review",
                "visibility": "hide",
                "context_window": 272_000,
                "supported_reasoning_levels": five_no_ultra,
            },
        ],
    }


def test_discover_codex_models_filters_visible_and_formats_display_name(
    tmp_path: Path,
) -> None:
    cache_file = tmp_path / "models_cache.json"
    cache_file.write_text(
        json.dumps(_sample_models_cache_payload()), encoding="utf-8"
    )

    models = discover_codex_models(tmp_path)

    assert len(models) == 8
    assert [m.id for m in models] == [
        "gpt-6.1-sol",
        "gpt-6-astra",
        "gpt-6.1-terra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert models[0] == CliModelInfo(
        id="gpt-6.1-sol",
        display_name="GPT-6.1-Sol (Assinatura Codex)",
        context_window=500_000,
        supported_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
    )
    assert models[4] == CliModelInfo(
        id="gpt-6-luna",
        display_name="GPT-6-Luna (Assinatura Codex)",
        context_window=272_000,
        supported_efforts=("low", "medium", "high", "xhigh", "max"),
    )


def test_discover_codex_models_falls_back_on_missing_or_corrupt_cache(
    tmp_path: Path,
) -> None:
    # Missing file
    assert discover_codex_models(tmp_path) == FALLBACK_CODEX_MODELS

    # Corrupt JSON
    cache_file = tmp_path / "models_cache.json"
    cache_file.write_text("{not valid json", encoding="utf-8")
    assert discover_codex_models(tmp_path) == FALLBACK_CODEX_MODELS

    # Empty or only hidden models
    cache_file.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-reserve", "visibility": "hide"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert discover_codex_models(tmp_path) == FALLBACK_CODEX_MODELS


def test_context_window_for_known_prefixed_and_unknown_models() -> None:
    assert context_window_for("gpt-6.1-sol", FALLBACK_CODEX_MODELS) == 500_000
    assert (
        context_window_for("openai-codex/gpt-6.1-terra", FALLBACK_CODEX_MODELS)
        == 500_000
    )
    assert context_window_for("gpt-6-astra", FALLBACK_CODEX_MODELS) == 272_000
    assert (
        context_window_for("unknown-codex-model", FALLBACK_CODEX_MODELS)
        == DEFAULT_CODEX_CONTEXT_WINDOW
    )
    assert (
        context_window_for(None, FALLBACK_CODEX_MODELS)
        == DEFAULT_CODEX_CONTEXT_WINDOW
    )


def test_resolve_codex_home_uses_env_or_userprofile(tmp_path: Path) -> None:
    custom = tmp_path / "custom_codex"
    assert resolve_codex_home({"CODEX_HOME": str(custom)}) == custom
    assert (
        resolve_codex_home({"USERPROFILE": str(tmp_path)})
        == tmp_path / ".codex"
    )
    assert resolve_codex_home({"HOME": str(tmp_path)}) == tmp_path / ".codex"


def test_discover_codex_models_refuses_symlinked_models_cache(
    tmp_path: Path, monkeypatch: object
) -> None:
    cache_file = tmp_path / "models_cache.json"
    cache_file.write_text(
        json.dumps(_sample_models_cache_payload()), encoding="utf-8"
    )
    real_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        if self.name == "models_cache.json":
            return True
        return real_is_symlink(self)

    import pytest

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(Path, "is_symlink", fake_is_symlink)
        assert discover_codex_models(tmp_path) == FALLBACK_CODEX_MODELS
    finally:
        mp.undo()


def test_discover_codex_models_handles_adversarial_and_edge_case_entries(
    tmp_path: Path,
) -> None:
    cache_file = tmp_path / "models_cache.json"

    # Empty file -> fallback
    cache_file.write_text("", encoding="utf-8")
    assert discover_codex_models(tmp_path) == FALLBACK_CODEX_MODELS

    # Root is array instead of object -> fallback
    cache_file.write_text("[]", encoding="utf-8")
    assert discover_codex_models(tmp_path) == FALLBACK_CODEX_MODELS

    # Mixed invalid/adversarial entries + one valid entry with bool context_window & already-suffixed display_name
    cache_file.write_text(
        json.dumps(
            {
                "models": [
                    None,
                    "invalid-entry",
                    {
                        "slug": "-evil-flag",
                        "display_name": "Evil",
                        "visibility": "list",
                    },
                    {
                        "slug": "bad slug\n",
                        "display_name": "Bad",
                        "visibility": "list",
                    },
                    {
                        "slug": "gpt-6.1-sol",
                        "display_name": "GPT-6.1-Sol (Assinatura Codex)",
                        "visibility": "list",
                        "context_window": True,  # bool is int subclass in Python!
                        "supported_reasoning_levels": [
                            "low",
                            {"effort": "HIGH"},
                            {"effort": ""},
                            123,
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    models = discover_codex_models(tmp_path)
    assert len(models) == 1
    assert models[0] == CliModelInfo(
        id="gpt-6.1-sol",
        display_name="GPT-6.1-Sol (Assinatura Codex)",
        context_window=DEFAULT_CODEX_CONTEXT_WINDOW,
        supported_efforts=("low", "high"),
    )

