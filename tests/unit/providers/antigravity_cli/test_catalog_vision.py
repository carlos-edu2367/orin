"""Antigravity models that see images are catalogued as such.

agy's stream-json input accepts only text, but an image returned in an MCP tool
result reaches the model (checked against agy 1.5.3). Orin's view_file returns
the picture that way, so Gemini and Claude must be listed with the ``image``
input modality; otherwise the composer says "this model cannot see" and every
attachment is routed to the visual-reading model instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, update

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import metadata, provider_model_catalog
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderModelCatalogService
from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.antigravity_cli.catalog import (
    FALLBACK_ANTIGRAVITY_MODELS,
    antigravity_input_modalities,
)
from agentos.providers.cli_registry import CLI_PROVIDERS


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("gemini-4-argon-high", ("text", "image")),
        ("gemini-3.8-flash-low", ("text", "image")),
        ("claude-opus-5-5-medium", ("text", "image")),
        ("google-antigravity/gemini-4-argon-low", ("text", "image")),
        ("gpt-oss-120b-medium", ("text",)),
        ("some-future-model", ("text",)),
    ],
)
def test_input_modalities_follow_the_model_family(slug: str, expected: tuple[str, ...]) -> None:
    assert antigravity_input_modalities(slug) == expected


def test_fallback_catalog_declares_vision_for_gemini_and_claude() -> None:
    by_id = {model.id: model.input_modalities for model in FALLBACK_ANTIGRAVITY_MODELS}
    assert by_id["gemini-4-argon-high"] == ("text", "image")
    assert by_id["claude-sonnet-5-5-low"] == ("text", "image")
    assert by_id["gpt-oss-120b-medium"] == ("text",)
    assert all(model.input_modalities for model in FALLBACK_ANTIGRAVITY_MODELS)


def test_adapter_says_images_reach_the_model_through_tool_results() -> None:
    assert AntigravityCliAdapter.capabilities.images_in_tool_results is True


def _service(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("test-secret-key-for-cli-registry-32b")
    PostgresProviderConfigurationAdapter(engine, cipher=cipher).configure(
        {
            "provider": "google-antigravity",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
            "risk_acknowledged": True,
        }
    )
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", AntigravityCliAdapter)
    service = ProviderModelCatalogService(PostgresProviderCatalogRepository(engine, cipher=cipher))
    return engine, service, ProviderCatalogContext(user_id="user_1", purpose="provider.catalog")


def test_list_refreshes_a_catalog_stored_as_text_only_and_keeps_favorites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    service.favorite(ctx, "google-antigravity", "gemini-4-argon-high", True)
    # The catalog an older Orin stored: every model text-only.
    with engine.begin() as connection:
        connection.execute(update(provider_model_catalog).values(input_modalities=["text"]))

    models = {item.model_id: item for item in service.list(ctx, "google-antigravity")}

    assert models["gemini-4-argon-high"].input_modalities == ("text", "image")
    assert models["gpt-oss-120b-medium"].input_modalities == ("text",)
    assert models["gemini-4-argon-high"].is_favorite is True


def test_a_text_only_custom_model_does_not_force_a_refresh_on_every_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    service.add_custom(ctx, "google-antigravity", "gemini-custom-preview")
    with engine.begin() as connection:
        connection.execute(
            update(provider_model_catalog)
            .where(provider_model_catalog.c.model_id == "gemini-custom-preview")
            .values(input_modalities=["text"])
        )
    calls: list[str] = []
    original = service.refresh
    monkeypatch.setattr(service, "refresh", lambda c, p: calls.append(p) or original(c, p))

    service.list(ctx, "google-antigravity")

    assert calls == []


def test_a_custom_gemini_model_is_added_as_seeing_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    record = service.add_custom(ctx, "google-antigravity", "gemini-custom-preview")
    assert record.input_modalities == ("text", "image")
