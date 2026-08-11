from __future__ import annotations

from datetime import UTC, datetime

from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord, PricingSummary
from agentos.provider_catalog.resolver_adapter import ProviderModelDescriptorAdapter
from agentos.providers.models import InputKind, ModelRef, ProviderRef


class Catalog:
    def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False):
        if provider != "openrouter":
            return []
        return [ProviderModelRecord(
            "openrouter", "anthropic/model-a", "Model A", 128000, ("tools",), ("text", "image"), ("text",),
            PricingSummary("3", "15"), datetime(2026, 8, 10, tzinfo=UTC),
        )]


def test_adapter_maps_a_provider_qualified_catalog_record_to_the_existing_model_domain() -> None:
    adapter = ProviderModelDescriptorAdapter(Catalog())

    descriptor = adapter.get_model(ProviderCatalogContext("user-a", "agent.configure"), "openrouter", "anthropic/model-a")

    assert descriptor.model_ref == ModelRef("catalog:openrouter:anthropic/model-a")
    assert descriptor.provider_ref == ProviderRef("provider:openrouter")
    assert descriptor.tools.supported is True
    assert InputKind.IMAGE in descriptor.compatibility.supported_input_kinds
    assert str(descriptor.cost.input_per_million_tokens) == "3"
