from __future__ import annotations

import inspect
from datetime import UTC, datetime

import httpx
import pytest

from agentos.provider_catalog import resolver_catalog
from agentos.provider_catalog.models import (
    PROVIDERS_WITH_BASE_URL,
    PROVIDERS_WITH_OPTIONAL_KEY,
    ProviderCatalogContext,
    ProviderModelRecord,
)
from agentos.provider_catalog.nvidia import (
    DEFAULT_NVIDIA_BASE_URL,
    DEFAULT_NVIDIA_LOCAL_BASE_URL,
    NVIDIA_CLOUD_BASE_URL,
    NvidiaAuthenticationError,
    NvidiaModelCatalogClient,
    is_nvidia_cloud,
    normalize_nvidia_base_url,
)
from agentos.provider_catalog.pricing import (
    _match_pricing,
    estimate_cost_usd,
    resolve_rates_per_million,
)
from agentos.provider_catalog.service import ProviderModelCatalogService


def test_nvidia_constants_and_provider_sets() -> None:
    assert NVIDIA_CLOUD_BASE_URL == "https://integrate.api.nvidia.com/v1"
    assert DEFAULT_NVIDIA_LOCAL_BASE_URL == "http://localhost:8000/v1"
    assert DEFAULT_NVIDIA_BASE_URL == NVIDIA_CLOUD_BASE_URL
    assert "nvidia" in PROVIDERS_WITH_BASE_URL
    assert "nvidia" in PROVIDERS_WITH_OPTIONAL_KEY


def test_normalize_nvidia_base_url_defaults_empty_to_cloud() -> None:
    assert normalize_nvidia_base_url("") == "https://integrate.api.nvidia.com/v1"
    assert normalize_nvidia_base_url("   ") == "https://integrate.api.nvidia.com/v1"


def test_normalize_nvidia_base_url_appends_v1_when_path_is_root() -> None:
    assert normalize_nvidia_base_url("https://integrate.api.nvidia.com") == "https://integrate.api.nvidia.com/v1"
    assert normalize_nvidia_base_url("http://localhost:8000/") == "http://localhost:8000/v1"


def test_normalize_nvidia_base_url_preserves_custom_subpath() -> None:
    assert normalize_nvidia_base_url("https://nim.internal.corp/api/v1/") == "https://nim.internal.corp/api/v1"


def test_normalize_nvidia_base_url_rejects_invalid_urls_and_insecure_nvidia_cloud() -> None:
    with pytest.raises(ValueError, match="http or https"):
        normalize_nvidia_base_url("ftp://integrate.api.nvidia.com/v1")
    with pytest.raises(ValueError, match="credentials"):
        normalize_nvidia_base_url("https://user:pass@integrate.api.nvidia.com/v1")
    with pytest.raises(ValueError, match="query or fragment"):
        normalize_nvidia_base_url("https://integrate.api.nvidia.com/v1?foo=bar")
    with pytest.raises(ValueError, match="https"):
        normalize_nvidia_base_url("http://integrate.api.nvidia.com/v1")


def test_is_nvidia_cloud_detects_official_nvidia_domains() -> None:
    assert is_nvidia_cloud("https://integrate.api.nvidia.com/v1") is True
    assert is_nvidia_cloud("https://api.nvcf.nvidia.com/v2") is True
    assert is_nvidia_cloud("http://localhost:8000/v1") is False
    assert is_nvidia_cloud("https://evil-nvidia.com/v1") is False


def test_nvidia_catalog_client_fetches_models_and_sends_bearer_token() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "meta/llama-3.1-70b-instruct", "owned_by": "meta", "context_length": 131072},
                    {"id": "deepseek-ai/deepseek-r1", "owned_by": "deepseek-ai"},
                ]
            },
        )

    client = NvidiaModelCatalogClient(transport=httpx.MockTransport(handler))
    payload = client.fetch_models("nvapi-test-key", base_url="https://integrate.api.nvidia.com/v1")

    assert seen_headers.get("authorization") == "Bearer nvapi-test-key"
    assert len(payload["data"]) == 2


def test_nvidia_catalog_client_raises_auth_error_on_401_402_403() -> None:
    for status in (401, 402, 403):
        client = NvidiaModelCatalogClient(
            transport=httpx.MockTransport(lambda req, s=status: httpx.Response(s, json={"detail": "denied"}))
        )
        with pytest.raises(NvidiaAuthenticationError) as exc_info:
            client.fetch_models("nvapi-bad-secret", base_url="https://integrate.api.nvidia.com/v1")
        assert "nvapi-bad-secret" not in str(exc_info.value)


def test_service_normalizes_nvidia_models_with_reasoning_and_vision_heuristics() -> None:
    service = ProviderModelCatalogService.__new__(ProviderModelCatalogService)
    models = service._normalize(
        "nvidia",
        {
            "data": [
                {"id": "deepseek-ai/deepseek-r1", "context_length": 65536},
                {"id": "meta/llama-3.2-90b-vision-instruct", "context_length": 131072},
                {"id": "nvidia/llama-3.1-nemotron-70b-instruct"},
            ]
        },
    )
    by_id = {m.model_id: m for m in models}
    assert by_id["deepseek-ai/deepseek-r1"].supports_thinking is True
    assert by_id["deepseek-ai/deepseek-r1"].context_window == 65536
    assert by_id["meta/llama-3.2-90b-vision-instruct"].supports_vision is True
    assert by_id["nvidia/llama-3.1-nemotron-70b-instruct"].supports_tools is True


class _FakeNvidiaRepo:
    def __init__(self, credential_row: dict[str, object]) -> None:
        self._credential = credential_row
        self.saved: list[ProviderModelRecord] = []

    def credential(self, context: ProviderCatalogContext, provider: str) -> dict[str, object] | None:
        return self._credential if provider == "nvidia" else None

    def replace(
        self,
        context: ProviderCatalogContext,
        provider: str,
        records: list[ProviderModelRecord],
        refreshed_at: datetime,
    ) -> None:
        self.saved = list(records)

    def list(
        self,
        context: ProviderCatalogContext,
        provider: str,
        favorites_only: bool = False,
    ) -> list[ProviderModelRecord]:
        return list(self.saved)

    def set_favorite(
        self,
        context: ProviderCatalogContext,
        provider: str,
        model_id: str,
        favorite: bool,
    ) -> ProviderModelRecord:
        raise NotImplementedError

    def add_custom(
        self,
        context: ProviderCatalogContext,
        provider: str,
        record: ProviderModelRecord,
    ) -> ProviderModelRecord:
        raise NotImplementedError

    def remove_custom(self, context: ProviderCatalogContext, provider: str, model_id: str) -> None:
        raise NotImplementedError


def test_service_refresh_nvidia_requires_api_key_for_cloud_and_allows_empty_for_self_hosted() -> None:
    ctx = ProviderCatalogContext(user_id="user-1", purpose="provider.catalog.refresh")
    cloud_repo = _FakeNvidiaRepo(
        {"enabled": True, "api_key": "", "base_url": "https://integrate.api.nvidia.com/v1"}
    )
    client = NvidiaModelCatalogClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"data": [{"id": "meta/llama-3.1-70b-instruct", "context_length": 131072}]},
            )
        )
    )
    cloud_service = ProviderModelCatalogService(
        cloud_repo, {}, nvidia_client=client, now=lambda: datetime(2026, 10, 25, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="provider API key is required for NVIDIA NIM Cloud"):
        cloud_service.refresh(ctx, "nvidia")

    local_repo = _FakeNvidiaRepo(
        {"enabled": True, "api_key": "", "base_url": "http://localhost:8000/v1"}
    )
    local_service = ProviderModelCatalogService(
        local_repo, {}, nvidia_client=client, now=lambda: datetime(2026, 10, 25, tzinfo=UTC)
    )
    receipt = local_service.refresh(ctx, "nvidia")
    assert receipt.count == 1
    assert local_repo.saved[0].model_id == "meta/llama-3.1-70b-instruct"
    assert local_repo.saved[0].provider == "nvidia"


def test_pricing_matches_nvidia_prefixed_models() -> None:
    llama_rates = resolve_rates_per_million("nvidia", "meta/llama-3.1-70b-instruct")
    deepseek_rates = resolve_rates_per_million("nvidia", "deepseek-ai/deepseek-r1")
    assert llama_rates is not None and llama_rates[0] > 0 and llama_rates[1] > 0
    assert deepseek_rates == (0.55, 2.19)
    assert _match_pricing("meta/llama-3.1-70b-instruct") == llama_rates
    assert _match_pricing("deepseek-ai/deepseek-r1") == deepseek_rates
    cost = estimate_cost_usd(
        provider="nvidia",
        model_id="meta/llama-3.1-70b-instruct",
        input_tokens=100_000,
        output_tokens=50_000,
    )
    assert cost is not None and cost > 0


def test_resolver_catalog_includes_nvidia() -> None:
    listing = inspect.getsource(resolver_catalog.PostgresProviderModelCatalog.list_models)
    assert '"nvidia"' in listing


def test_normalize_nvidia_base_url_strips_accidental_endpoint_suffixes() -> None:
    assert (
        normalize_nvidia_base_url("https://integrate.api.nvidia.com/v1/models")
        == "https://integrate.api.nvidia.com/v1"
    )
    assert (
        normalize_nvidia_base_url("https://integrate.api.nvidia.com/v1/chat/completions/")
        == "https://integrate.api.nvidia.com/v1"
    )
    assert (
        normalize_nvidia_base_url("http://localhost:8000/models")
        == "http://localhost:8000/v1"
    )


def test_nvidia_empty_api_key_preserves_pool_keys_and_all_exhausted_tier3_refresh_sanitizes_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    from agentos.provider_catalog.service import ProviderCatalogUnavailable

    engine = create_engine("sqlite://")
    metadata.create_all(
        engine,
        tables=[provider_configurations, provider_api_keys, provider_models, provider_model_favorites],
    )
    cipher = ProviderSecretCipher(b"0" * 32)
    keys = PostgresProviderApiKeyAdapter(engine, cipher=cipher)
    config = PostgresProviderConfigurationAdapter(engine, cipher=cipher, key_pool=keys)

    k1 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-secret-tier3-a", "label": "Trial A"})
    k2 = keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-secret-tier3-b", "label": "Trial B"})
    keys.mark_exhausted(k1["id"])
    keys.mark_exhausted(k2["id"])

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
    pool_after_save = keys.list_keys({"user_id": "u1", "provider": "nvidia"})
    assert [row["id"] for row in pool_after_save] == [k1["id"], k2["id"]]

    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen_auth.append(auth)
        return httpx.Response(
            402,
            json={"error": {"message": f"Out of credits for {auth}"}},
            request=request,
        )

    client = NvidiaModelCatalogClient(transport=httpx.MockTransport(handler))
    repo = PostgresProviderCatalogRepository(engine, cipher=cipher, keys=keys)
    service = ProviderModelCatalogService(repo, {}, nvidia_client=client)
    ctx = ProviderCatalogContext(user_id="u1", purpose="provider.catalog.refresh")

    with pytest.raises(ProviderCatalogUnavailable) as exc_info:
        service.refresh(ctx, "nvidia")

    assert seen_auth == ["Bearer nvapi-secret-tier3-a", "Bearer nvapi-secret-tier3-b"]
    assert "nvapi-secret-tier3-a" not in str(exc_info.value)
    assert "nvapi-secret-tier3-b" not in str(exc_info.value)
    assert "nvapi-secret-tier3-a" not in repr(exc_info.value.__cause__)
    assert "nvapi-secret-tier3-b" not in repr(exc_info.value.__cause__)
    assert "nvapi-secret-tier3-a" not in caplog.text
    assert "nvapi-secret-tier3-b" not in caplog.text
