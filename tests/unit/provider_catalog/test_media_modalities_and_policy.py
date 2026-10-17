from __future__ import annotations

from datetime import UTC, datetime
import pytest

from agentos.provider_catalog.modalities import (
    SameProviderMediaModelPolicy,
    infer_output_modalities,
)
from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord
from agentos.provider_catalog.service import ProviderModelCatalogService


class _FakeRepo:
    def __init__(self) -> None:
        self.custom_records: list[ProviderModelRecord] = []

    def credential(self, context, provider):
        return {"enabled": True, "api_key": "sk-test", "base_url": ""}

    def replace(self, context, provider, records, refreshed_at):
        self.custom_records = list(records)

    def list(self, context, provider, favorites_only=False):
        return list(self.custom_records)

    def set_favorite(self, context, provider, model_id, favorite):
        raise NotImplementedError

    def add_custom(self, context, provider, record: ProviderModelRecord) -> ProviderModelRecord:
        self.custom_records.append(record)
        return record

    def remove_custom(self, context, provider, model_id):
        raise NotImplementedError


def test_infer_output_modalities_from_known_model_patterns() -> None:
    assert "image" in infer_output_modalities("gpt-image-1")
    assert "image" in infer_output_modalities("dall-e-3")
    assert "image" in infer_output_modalities("black-forest-labs/flux-1.1-pro")
    assert "image" in infer_output_modalities("google/gemini-2.5-flash-image-preview")
    assert "audio" in infer_output_modalities("tts-1-hd")
    assert "audio" in infer_output_modalities("gpt-4o-mini-tts")
    assert "audio" in infer_output_modalities("openai/gpt-4o-audio-preview")
    assert "video" in infer_output_modalities("sora-1.0")
    assert "video" in infer_output_modalities("google/veo-2")
    assert "video" in infer_output_modalities("kling-v1-6")
    assert infer_output_modalities("gpt-4o") == ("text",)


def test_infer_output_modalities_preserves_explicit_catalog_modalities() -> None:
    assert infer_output_modalities("custom-model-x", ("image",)) == ("image",)
    assert infer_output_modalities("custom-model-y", ("text", "audio")) == ("text", "audio")
    # Invalid/malformed modalities are ignored safely
    assert infer_output_modalities("gpt-4o", ("invalid", "", "  ")) == ("text",)


def test_same_provider_media_policy_enforces_favorites_and_resolves_defaults() -> None:
    policy = SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="openai",
        favorite_models=[
            ("openai", "gpt-image-1", ("image",)),
            ("openai", "dall-e-3", ("image",)),
            ("openai", "tts-1", ("audio",)),
            ("openrouter", "sora-1.0", ("video",)),  # Different provider -> must be ignored!
            ("openai", "gpt-image-1:batch", ("image",)),  # Batch model -> must be ignored!
        ],
    )
    assert policy.available_modalities() == frozenset({"image", "audio"})
    assert policy.models_for("image") == ("gpt-image-1", "dall-e-3")
    assert policy.models_for("video") == ()
    assert policy.resolve_model("image") == "gpt-image-1"
    assert policy.resolve_model("image", "dall-e-3") == "dall-e-3"

    with pytest.raises(PermissionError, match="not a favorited image model"):
        policy.resolve_model("image", "flux-pro")

    with pytest.raises(PermissionError, match="no favorited video model"):
        policy.resolve_model("video")


def test_service_add_custom_accepts_output_modalities_and_infers_by_name() -> None:
    repo = _FakeRepo()
    service = ProviderModelCatalogService(repo, upstreams={}, now=lambda: datetime(2026, 10, 17, tzinfo=UTC))
    ctx = ProviderCatalogContext("u1", "test")

    explicit = service.add_custom(ctx, "openai", "my-custom-synth", "Synth", output_modalities=["audio"])
    assert explicit.output_modalities == ("audio",)

    inferred = service.add_custom(ctx, "openai", "dall-e-3")
    assert "image" in inferred.output_modalities

    with pytest.raises(ValueError, match="output_modalities"):
        service.add_custom(ctx, "openai", "bad-mod", output_modalities=["hologram"])
