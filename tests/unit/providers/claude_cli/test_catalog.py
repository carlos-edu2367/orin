from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.providers.claude_cli.catalog import (
    FAMILY_DEFAULTS,
    CatalogModel,
    ClaudeCliModelCatalogClient,
    context_window_for,
    discover_cached_models,
    resolve_family_alias,
)


def test_catalog_keeps_5x_ids_and_reads_window(tmp_path: Path) -> None:
    (tmp_path / "org-cc.json").write_text(
        json.dumps(
            {
                "version": 2,
                "fetchedAt": 1793201481996,
                "organizationUuid": "org-1",
                "catalog": {
                    "surface": "cc",
                    "config": {
                        "id": "cc",
                        "models": [
                            {
                                "id": "claude-opus-5-5",
                                "name": "Opus 5.5",
                                "runtime": {"max_input_tokens": 1_000_000},
                            },
                            {
                                "id": "claude-sonnet-5-5",
                                "name": "Sonnet 5.5",
                                "runtime": {"max_input_tokens": 200_000},
                            },
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    models = discover_cached_models(cache_dir=tmp_path)
    assert [m.id for m in models][:1] == ["claude-opus-5-5"]
    assert context_window_for("claude-opus-5-5", models) == 1_000_000
    assert context_window_for("claude-sonnet-5-5", models) == 200_000


def test_resolve_family_alias_prefers_newest_catalog_member_then_default() -> None:
    models = [
        CatalogModel("claude-opus-5-5", "Opus 5.5", 1_000_000),
        CatalogModel("claude-opus-5-6", "Opus 5.6", 1_000_000),
    ]
    assert resolve_family_alias("opus", models) == "claude-opus-5-6"
    assert resolve_family_alias("fable", []) == "claude-fable-5-1"
    assert resolve_family_alias("gpt", []) is None


def test_resolve_family_alias_does_not_downgrade_5x_to_stale_4x() -> None:
    stale_models = [
        CatalogModel("claude-opus-4-7", "Opus 4.7", 200_000),
    ]
    assert resolve_family_alias("opus", stale_models) == "claude-opus-5-5"


def test_catalog_client_falls_back_to_5x_defaults_with_1m_for_opus_55(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.providers.claude_cli.catalog.check_claude_cli_status",
        lambda **kwargs: {"loggedIn": True, "authMethod": "claude.ai", "orgId": "org-empty"},
    )
    monkeypatch.setattr(
        "agentos.providers.claude_cli.catalog._fetch_published_claude_cli_models",
        lambda **kwargs: [],
    )
    client = ClaudeCliModelCatalogClient(cache_dir=tmp_path / "empty")
    rows = client.fetch("")
    by_id = {str(r["id"]): r for r in rows}
    for model_id in FAMILY_DEFAULTS.values():
        assert model_id in by_id
    assert by_id["claude-opus-5-5"]["context_length"] == 1_000_000
    assert by_id["claude-sonnet-5-5"]["context_length"] == 200_000



def test_context_window_for_handles_multi_slash_or_malformed_model_id() -> None:
    assert context_window_for("openrouter/anthropic/claude-opus-5-5") == 1_000_000
    assert context_window_for("invalid model id; rm -rf /") == 200_000
