from datetime import UTC, datetime

from sqlalchemy import create_engine, insert, update

from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog, provider_model_favorites
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import ProviderCatalogContext


def test_catalog_rows_are_hidden_when_the_saved_url_does_not_match_the_catalog_source() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_configurations, provider_model_catalog, provider_model_favorites])
    cipher = ProviderSecretCipher(b"0" * 32)
    configuration = PostgresProviderConfigurationAdapter(engine, cipher=cipher)
    configuration.configure({
        "provider": "ollama", "user_id": "user-1", "enabled": True,
        "api_key": "", "base_url": "http://localhost:11434",
    })
    now = datetime(2026, 8, 12, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(update(provider_configurations).values(catalog_refreshed_at=now))
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="ollama", model_id="local-model", display_name="Local model",
            catalog_base_url="http://localhost:11434",
            context_window=8192, capabilities=[], input_modalities=["text"], output_modalities=["text"],
            input_per_million=None, output_per_million=None, route_kind="model",
            refreshed_at=now, created_at=now, updated_at=now,
        ))

    repository = PostgresProviderCatalogRepository(engine, cipher=cipher)
    context = ProviderCatalogContext("user-1", "provider.catalog.list")
    with engine.begin() as connection:
        connection.execute(update(provider_configurations).values(base_url="https://ollama.com"))

    assert repository.list(context, "ollama") == []
