from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.resolver_catalog import PostgresProviderModelCatalog
from agentos.provider_catalog.service import ProviderModelCatalogService
from agentos.providers.models import (
    AuthorizedModelListQuery,
    CancellationRequirement,
    FallbackRequest,
    InputKind,
    ModelRequirements,
    ModelResolutionRequest,
    ModelResolved,
    AuthorizedModelSelectionQuery,
    ProviderOperationContext,
    ResponseFormat,
)
from agentos.providers.resolver import ModelResolverService


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


class FakeOpenRouter:
    def fetch(self, api_key: str) -> list[dict[str, object]]:
        assert api_key == "test-key"
        return [{"id": "anthropic/test-model", "name": "Test model", "context_length": 128000, "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": [], "pricing": {"prompt": "0.000003", "completion": "0.000015"}}]


def test_postgres_backed_catalog_resolves_and_records_an_immutable_selection() -> None:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    PostgresProviderConfigurationAdapter(engine).configure({"provider": "openrouter", "user_id": "resolver-owner", "enabled": True, "api_key": "test-key"})
    refreshed_at = datetime(2026, 8, 10, tzinfo=UTC)
    ProviderModelCatalogService(PostgresProviderCatalogRepository(engine), {"openrouter": FakeOpenRouter()}, now=lambda: refreshed_at).refresh(ProviderCatalogContext("resolver-owner", "provider.catalog.refresh"), "openrouter")
    catalog = PostgresProviderModelCatalog(engine)
    context = ProviderOperationContext("resolver-owner", None, "agent-resolver", "execution-resolver", "correlation-resolver", "conversation.create", "actor-resolver")
    descriptor = catalog.list_models(AuthorizedModelListQuery(context)).items[0]
    resolver = ModelResolverService(catalog, clock=lambda: refreshed_at)

    outcome = resolver.resolve(ModelResolutionRequest(
        "resolver-request",
        ModelRequirements(
            context=context, preferred_model_ref=descriptor.model_ref, allowed_provider_refs=(descriptor.provider_ref,),
            allowed_model_refs=(descriptor.model_ref,), input_kinds=(InputKind.TEXT,), response_format=ResponseFormat.TEXT,
            cancellation_requirement=CancellationRequirement.ANY, minimum_context_tokens=1,
            maximum_input_tokens=1024, maximum_output_tokens=512, maximum_total_tokens=1536, fallback=FallbackRequest(),
        ),
        "resolver-idempotency",
    ))

    assert isinstance(outcome, ModelResolved)
    assert outcome.selection.primary.model_ref == descriptor.model_ref
    restored = catalog.inspect_selection(AuthorizedModelSelectionQuery(context, outcome.selection.selection_ref))
    snapshot = catalog.load_snapshot(outcome.selection.approved_requirements_ref, context)
    assert restored.primary.model_ref == descriptor.model_ref
    assert snapshot.allowed_model_refs == (descriptor.model_ref,)
    with engine.connect() as connection:
        stored = connection.exec_driver_sql("SELECT model_ref, model_revision FROM provider_model_selections WHERE user_id = 'resolver-owner'").one()
    assert stored == (str(descriptor.model_ref), str(descriptor.revision))
