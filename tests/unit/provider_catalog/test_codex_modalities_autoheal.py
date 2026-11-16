from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, update

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import metadata, provider_model_catalog
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import (
    ProviderModelCatalogService,
    _codex_modalities_are_stale,
)
from agentos.providers.cli_registry import CLI_PROVIDERS
from agentos.providers.codex_cli.adapter import CodexCliAdapter


def _service(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("test-secret-key-for-codex-autoheal")
    PostgresProviderConfigurationAdapter(engine, cipher=cipher).configure(
        {
            "provider": "openai-codex",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
        }
    )
    monkeypatch.setitem(CLI_PROVIDERS, "openai-codex", CodexCliAdapter)
    service = ProviderModelCatalogService(PostgresProviderCatalogRepository(engine, cipher=cipher))
    return engine, service, ProviderCatalogContext(user_id="user_1", purpose="provider.catalog")


def test_list_auto_heals_stale_text_only_codex_rows_and_preserves_favorites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "openai-codex")
    service.favorite(ctx, "openai-codex", "gpt-6.1-sol", True)
    with engine.begin() as connection:
        connection.execute(update(provider_model_catalog).values(input_modalities=["text"]))

    stale_rows = service._repository.list(ctx, "openai-codex", False)
    assert _codex_modalities_are_stale(stale_rows) is True

    models = {item.model_id: item for item in service.list(ctx, "openai-codex")}
    assert models["gpt-6.1-sol"].input_modalities == ("text", "image")
    assert models["gpt-6.1-sol"].is_favorite is True
    assert models["codex-image"].input_modalities == ("text",)
    assert _codex_modalities_are_stale(list(models.values())) is False


def test_custom_text_only_codex_model_does_not_trigger_refresh_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "openai-codex")
    service.add_custom(ctx, "openai-codex", "gpt-custom-codex")
    with engine.begin() as connection:
        connection.execute(
            update(provider_model_catalog)
            .where(provider_model_catalog.c.model_id == "gpt-custom-codex")
            .values(input_modalities=["text"])
        )

    calls: list[str] = []
    original = service.refresh
    monkeypatch.setattr(service, "refresh", lambda c, p: calls.append(p) or original(c, p))

    listed = service.list(ctx, "openai-codex")
    assert listed
    assert calls == []


def test_add_custom_codex_model_records_image_input_modality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "openai-codex")

    custom_chat = service.add_custom(ctx, "openai-codex", "gpt-6.5-custom")
    assert custom_chat.input_modalities == ("text", "image")

    custom_image_gen = service.add_custom(
        ctx,
        "openai-codex",
        "codex-image",
        output_modalities=["image"],
    )
    assert custom_image_gen.input_modalities == ("text",)


def test_models_cache_with_multimodal_and_text_only_model_does_not_loop_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6.1-sol",
                        "display_name": "GPT-6.1-Sol",
                        "visibility": "list",
                        "context_window": 500000,
                        "input_modalities": ["text", "image"],
                    },
                    {
                        "slug": "o3-mini",
                        "display_name": "o3-mini",
                        "visibility": "list",
                        "context_window": 200000,
                        "input_modalities": ["text"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    _, service, ctx = _service(monkeypatch)
    calls: list[str] = []
    original = service.refresh
    monkeypatch.setattr(service, "refresh", lambda c, p: calls.append(p) or original(c, p))

    first = {item.model_id: item for item in service.list(ctx, "openai-codex")}
    assert calls == ["openai-codex"]
    assert first["gpt-6.1-sol"].input_modalities == ("text", "image")
    assert first["o3-mini"].input_modalities == ("text",)
    assert _codex_modalities_are_stale(list(first.values())) is False

    second = {item.model_id: item for item in service.list(ctx, "openai-codex")}
    assert calls == ["openai-codex"]
    assert second["o3-mini"].input_modalities == ("text",)


def test_stale_rows_after_refresh_activate_backoff_and_stop_refresh_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home-stale"
    codex_home.mkdir()
    (codex_home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "o3-mini",
                        "display_name": "o3-mini",
                        "visibility": "list",
                        "context_window": 200000,
                        "input_modalities": ["text"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    _, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "openai-codex")

    calls: list[str] = []
    original = service.refresh
    monkeypatch.setattr(service, "refresh", lambda c, p: calls.append(p) or original(c, p))

    first = service.list(ctx, "openai-codex")
    assert [m.model_id for m in first if m.model_id != "codex-image"] == ["o3-mini"]
    assert calls == ["openai-codex"]

    second = service.list(ctx, "openai-codex")
    assert [m.model_id for m in second if m.model_id != "codex-image"] == ["o3-mini"]
    assert calls == ["openai-codex"]
