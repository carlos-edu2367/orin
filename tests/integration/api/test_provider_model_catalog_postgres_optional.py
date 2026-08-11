from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderModelCatalogService


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


class FakeOpenRouter:
    def fetch(self, api_key: str) -> list[dict[str, object]]:
        assert api_key == "test-key"
        return [{"id": "anthropic/test-model", "name": "Test model", "context_length": 128000, "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": [], "pricing": {}}]


def test_postgres_catalog_is_user_scoped_and_persists_only_sanitized_model_metadata() -> None:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    configuration = PostgresProviderConfigurationAdapter(engine)
    configuration.configure({"provider": "openrouter", "user_id": "catalog-owner", "enabled": True, "api_key": "test-key"})
    service = ProviderModelCatalogService(PostgresProviderCatalogRepository(engine), {"openrouter": FakeOpenRouter()}, now=lambda: datetime(2026, 8, 10, tzinfo=UTC))
    owner = ProviderCatalogContext("catalog-owner", "provider.catalog.refresh")
    stranger = ProviderCatalogContext("catalog-stranger", "provider.catalog.inspect")

    service.refresh(owner, "openrouter")

    items = service.list(owner, "openrouter")
    assert [item.model_id for item in items] == ["anthropic/test-model"]
    assert service.list(stranger, "openrouter") == []
    with engine.connect() as connection:
        raw = connection.exec_driver_sql("SELECT api_key, api_key_ciphertext FROM provider_configurations WHERE user_id = 'catalog-owner'").one()
        stored = connection.exec_driver_sql("SELECT model_id, display_name FROM provider_model_catalog WHERE user_id = 'catalog-owner'").one()
    assert raw[0] is None
    assert raw[1].startswith("enc:v1:")
    assert raw[1] != "test-key"
    assert stored == ("anthropic/test-model", "Test model")
