from __future__ import annotations

import httpx
import pytest

from agentos.provider_catalog.omniroute import OmniRouteCatalogClient, normalize_omniroute_base_url


def test_normalizes_documented_local_base_url_without_leaking_credentials() -> None:
    assert normalize_omniroute_base_url("http://localhost:20128/v1/") == "http://localhost:20128/v1"
    with pytest.raises(ValueError):
        normalize_omniroute_base_url("ftp://localhost:20128/v1")
    with pytest.raises(ValueError):
        normalize_omniroute_base_url("http://key@localhost:20128/v1")


def test_lists_openai_compatible_models_and_preserves_only_safe_metadata() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": [
            {"id": "auto/coding", "object": "model", "owned_by": "omniroute"},
            {"id": "kimi/k2", "object": "model", "context_length": 128000,
             "supported_parameters": ["tools", "stream"], "input_modalities": ["text", "image"]},
        ]})

    client = OmniRouteCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    models = client.fetch("omni-secret", base_url="http://localhost:20128/v1")

    assert captured["url"] == "http://localhost:20128/v1/models"
    assert captured["authorization"] == "Bearer omni-secret"
    assert [item["id"] for item in models] == ["auto/coding", "kimi/k2"]
    assert models[0]["route_kind"] == "auto"
    assert models[1]["route_kind"] == "model"
    assert "omni-secret" not in repr(client)


def test_connection_failure_is_a_sanitized_catalog_error() -> None:
    client = OmniRouteCatalogClient(client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(401, json={"error": {"message": "bad key"}}))))

    with pytest.raises(RuntimeError, match="OmniRoute connection failed") as error:
        client.fetch("omni-secret", base_url="http://localhost:20128/v1")

    assert "omni-secret" not in str(error.value)


def test_allows_documented_no_auth_gateway_connections() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": []})

    client = OmniRouteCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.fetch("", base_url="http://localhost:20128/v1") == []
