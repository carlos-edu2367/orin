from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import (
    metadata,
    provider_api_keys,
    provider_configurations,
    provider_model_favorites,
    provider_models,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.nvidia import NvidiaModelCatalogClient
from agentos.provider_catalog.service import ProviderModelCatalogService


def _setup():
    engine = create_engine("sqlite://")
    metadata.create_all(
        engine,
        tables=[provider_configurations, provider_api_keys, provider_models, provider_model_favorites],
    )
    cipher = ProviderSecretCipher(b"0" * 32)
    keys = PostgresProviderApiKeyAdapter(engine, cipher=cipher)
    config = PostgresProviderConfigurationAdapter(engine, cipher=cipher, key_pool=keys)
    return engine, keys, config


def test_nvidia_cloud_requires_api_key_when_no_keys_in_pool() -> None:
    _, _, config = _setup()
    with pytest.raises(ValueError, match="API key"):
        config.configure(
            {
                "user_id": "u1",
                "provider": "nvidia",
                "enabled": True,
                "api_key": "",
                "base_url": "https://integrate.api.nvidia.com/v1",
            }
        )


def test_nvidia_self_hosted_allows_empty_api_key() -> None:
    _, _, config = _setup()
    saved = config.configure(
        {
            "user_id": "u1",
            "provider": "nvidia",
            "enabled": True,
            "api_key": "",
            "base_url": "http://localhost:8000/v1",
        }
    )
    assert saved["provider"] == "nvidia"
    assert saved["base_url"] == "http://localhost:8000/v1"
    assert saved["has_secret"] is False


def test_configuring_provider_with_empty_api_key_preserves_existing_pool_keys() -> None:
    _, keys, config = _setup()
    keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-pool-key-1", "label": "Key 1"})
    keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-pool-key-2", "label": "Key 2"})

    saved = config.configure(
        {
            "user_id": "u1",
            "provider": "nvidia",
            "enabled": True,
            "api_key": "",
            "base_url": "https://integrate.api.nvidia.com/v1",
        }
    )

    assert saved["has_secret"] is True
    remaining = keys.list_keys({"user_id": "u1", "provider": "nvidia"})
    assert len(remaining) == 2
    assert [r["label"] for r in remaining] == ["Key 1", "Key 2"]


def test_test_connection_for_nvidia_returns_reachable_and_model_count() -> None:
    engine, keys, _ = _setup()
    cipher = ProviderSecretCipher(b"0" * 32)
    nvidia_client = NvidiaModelCatalogClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": [{"id": "meta/llama-3.1-70b-instruct"}]})
        )
    )
    config = PostgresProviderConfigurationAdapter(
        engine, cipher=cipher, key_pool=keys, nvidia_client=nvidia_client
    )

    result = config.test_connection(
        {
            "user_id": "u1",
            "provider": "nvidia",
            "api_key": "nvapi-valid-key",
            "base_url": "https://integrate.api.nvidia.com/v1",
        }
    )
    assert result == {"reachable": True, "model_count": 1, "authenticated": True}


def test_test_connection_for_nvidia_returns_unauthenticated_on_401_402_403() -> None:
    engine, keys, _ = _setup()
    cipher = ProviderSecretCipher(b"0" * 32)
    nvidia_client = NvidiaModelCatalogClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(402, json={"detail": "out of credits"}))
    )
    config = PostgresProviderConfigurationAdapter(
        engine, cipher=cipher, key_pool=keys, nvidia_client=nvidia_client
    )

    result = config.test_connection(
        {
            "user_id": "u1",
            "provider": "nvidia",
            "api_key": "nvapi-exhausted-key",
            "base_url": "https://integrate.api.nvidia.com/v1",
        }
    )
    assert result == {"reachable": True, "model_count": 0, "authenticated": False}


def test_catalog_refresh_rotates_keys_marking_exhausted_and_cooldown_until_success() -> None:
    engine, keys, config = _setup()
    cipher = ProviderSecretCipher(b"0" * 32)
    k0 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-dead-0", "label": "Key 0"})
    k1 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-ratelimited-1", "label": "Key 1"})
    k2 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-good-2", "label": "Key 2"})
    config.configure(
        {
            "user_id": "u1",
            "provider": "nvidia",
            "enabled": True,
            "api_key": "",
            "base_url": "https://integrate.api.nvidia.com/v1",
        }
    )

    seen_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen_auth_headers.append(auth)
        if auth == "Bearer nvapi-dead-0":
            return httpx.Response(402, json={"detail": "Credit balance exhausted"})
        if auth == "Bearer nvapi-ratelimited-1":
            return httpx.Response(429, json={"detail": "Too many requests"})
        return httpx.Response(
            200,
            json={"data": [{"id": "meta/llama-3.1-70b-instruct", "context_length": 131072}]},
        )

    nvidia_client = NvidiaModelCatalogClient(transport=httpx.MockTransport(handler))
    repository = PostgresProviderCatalogRepository(engine, cipher=cipher, keys=keys)
    service = ProviderModelCatalogService(
        repository,
        {},
        nvidia_client=nvidia_client,
        now=lambda: datetime(2026, 10, 26, tzinfo=UTC),
    )

    ctx = ProviderCatalogContext(user_id="u1", purpose="provider.catalog.refresh")
    receipt = service.refresh(ctx, "nvidia")

    assert receipt.count == 1
    assert seen_auth_headers == [
        "Bearer nvapi-dead-0",
        "Bearer nvapi-ratelimited-1",
        "Bearer nvapi-good-2",
    ]
    by_id = {item["id"]: item for item in keys.list_keys({"user_id": "u1", "provider": "nvidia"})}
    assert by_id[k0["id"]]["status"] == "exhausted"
    assert by_id[k0["id"]]["cooldown_until"] is None
    assert by_id[k1["id"]]["status"] == "cooldown"
    assert by_id[k1["id"]]["cooldown_until"] is not None
    assert by_id[k2["id"]]["status"] == "active"
    assert by_id[k2["id"]]["cooldown_until"] is None


def test_catalog_refresh_when_all_keys_fail_marks_all_and_sanitizes_error() -> None:
    from agentos.provider_catalog.service import ProviderCatalogUnavailable

    engine, keys, config = _setup()
    cipher = ProviderSecretCipher(b"0" * 32)
    k0 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-secret-dead-0", "label": "Key 0"})
    k1 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-secret-dead-1", "label": "Key 1"})
    config.configure(
        {
            "user_id": "u1",
            "provider": "nvidia",
            "enabled": True,
            "api_key": "",
            "base_url": "https://integrate.api.nvidia.com/v1",
        }
    )

    nvidia_client = NvidiaModelCatalogClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"detail": "Invalid key"}))
    )
    repository = PostgresProviderCatalogRepository(engine, cipher=cipher, keys=keys)
    service = ProviderModelCatalogService(repository, {}, nvidia_client=nvidia_client)

    ctx = ProviderCatalogContext(user_id="u1", purpose="provider.catalog.refresh")
    with pytest.raises(ProviderCatalogUnavailable) as exc_info:
        service.refresh(ctx, "nvidia")

    assert "nvapi-secret-dead-0" not in str(exc_info.value)
    assert "nvapi-secret-dead-1" not in str(exc_info.value)
    by_id = {item["id"]: item for item in keys.list_keys({"user_id": "u1", "provider": "nvidia"})}
    assert by_id[k0["id"]]["status"] == "exhausted"
    assert by_id[k1["id"]]["status"] == "exhausted"
