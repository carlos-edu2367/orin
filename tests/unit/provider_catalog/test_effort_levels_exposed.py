from __future__ import annotations

from datetime import UTC, datetime

from agentos.api.gateway import _catalog_model_public
from agentos.provider_catalog.models import ProviderModelRecord
from agentos.provider_catalog.service import ProviderModelCatalogService


class _FakeRepo:
    def __init__(self, records: list[ProviderModelRecord]) -> None:
        self._records = records

    def credential(self, context, provider):
        return {"enabled": True, "api_key": "", "risk_acknowledged_at": "2026-01-01T00:00:00Z"}

    def list(self, context, provider, favorites_only=False):
        return [r for r in self._records if r.provider == provider]

    def replace(self, context, provider, records, refreshed_at):
        self._records = [r for r in self._records if r.provider != provider] + list(records)

    def set_favorite(self, context, provider, model_id, favorite):
        raise NotImplementedError

    def add_custom(self, context, provider, record):
        raise NotImplementedError


def test_provider_catalog_exposes_supported_efforts_and_default_effort() -> None:
    now = datetime.now(UTC)
    records = [
        ProviderModelRecord(
            provider="anthropic",
            model_id="claude-opus-5-5",
            display_name="Claude Opus 5.5",
            context_window=1_000_000,
            capabilities=("tools", "reasoning"),
            input_modalities=("text", "image"),
            output_modalities=("text",),
            pricing=None,
            refreshed_at=now,
        ),
        ProviderModelRecord(
            provider="anthropic",
            model_id="claude-haiku-4-5-20251001",
            display_name="Claude Haiku 4.5",
            context_window=200_000,
            capabilities=("tools",),
            input_modalities=("text", "image"),
            output_modalities=("text",),
            pricing=None,
            refreshed_at=now,
        ),
        ProviderModelRecord(
            provider="openai-codex",
            model_id="gpt-6.1-sol",
            display_name="GPT-6.1-Sol",
            context_window=500_000,
            capabilities=("tools", "reasoning"),
            input_modalities=("text", "image"),
            output_modalities=("text",),
            pricing=None,
            refreshed_at=now,
        ),
        ProviderModelRecord(
            provider="google-antigravity",
            model_id="gemini-4-argon-high",
            display_name="Gemini 4 Argon (High)",
            context_window=1_000_000,
            capabilities=("tools", "reasoning"),
            input_modalities=("text", "image"),
            output_modalities=("text",),
            pricing=None,
            refreshed_at=now,
        ),
    ]
    repo = _FakeRepo(records)
    service = ProviderModelCatalogService(repo)
    from agentos.provider_catalog.models import ProviderCatalogContext

    ctx = ProviderCatalogContext("u1", "provider.catalog.list")
    anth = {m.model_id: m for m in service.list(ctx, "anthropic")}
    assert anth["claude-opus-5-5"].supported_efforts == ("low", "medium", "high", "xhigh", "max")
    assert anth["claude-opus-5-5"].default_effort == "medium"
    assert anth["claude-haiku-4-5-20251001"].supported_efforts == ()
    assert anth["claude-haiku-4-5-20251001"].default_effort is None

    pub = _catalog_model_public(anth["claude-opus-5-5"])
    assert pub["supported_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert pub["default_effort"] == "medium"

    codex = {m.model_id: m for m in service.list(ctx, "openai-codex")}
    assert "ultra" in codex["gpt-6.1-sol"].supported_efforts

    agy = {m.model_id: m for m in service.list(ctx, "google-antigravity")}
    assert agy["gemini-4-argon-high"].supported_efforts == ("high",)
    assert agy["gemini-4-argon-high"].default_effort == "high"
