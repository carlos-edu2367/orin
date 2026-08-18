from __future__ import annotations

import httpx
import pytest

from agentos.provider_catalog.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    OLLAMA_CLOUD_BASE_URL,
    OllamaCatalogClient,
    is_ollama_cloud,
    normalize_ollama_base_url,
    OllamaCloudAuthenticationError,
)


def test_normalizes_the_local_default_without_leaking_credentials() -> None:
    assert normalize_ollama_base_url("http://localhost:11434/") == "http://localhost:11434"
    assert normalize_ollama_base_url(DEFAULT_OLLAMA_BASE_URL) == DEFAULT_OLLAMA_BASE_URL
    with pytest.raises(ValueError):
        normalize_ollama_base_url("ftp://localhost:11434")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("http://key@localhost:11434")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("http://localhost:11434?token=secret")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("   ")


def test_strips_an_api_or_v1_suffix_the_user_may_have_pasted() -> None:
    """The native API lives at /api/*, so the base URL must be the bare origin."""
    assert normalize_ollama_base_url("http://localhost:11434/v1") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/api/") == "http://localhost:11434"
    assert normalize_ollama_base_url("https://gpu.lan/ollama/v1") == "https://gpu.lan/ollama"


def test_cloud_is_recognized_by_host_not_by_a_stored_mode_flag() -> None:
    assert is_ollama_cloud(OLLAMA_CLOUD_BASE_URL) is True
    assert is_ollama_cloud("https://api.ollama.com") is True
    assert is_ollama_cloud(DEFAULT_OLLAMA_BASE_URL) is False
    assert is_ollama_cloud("http://gpu.lan:11434") is False
    assert is_ollama_cloud("https://notollama.com") is False


def _handler(seen: list[str], *, show_fails_for: str | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3:8b", "model": "qwen3:8b", "details": {"family": "qwen3"}},
                {"name": "llava:7b", "model": "llava:7b", "details": {"family": "llava"}},
            ]})
        import json as _json

        model = _json.loads(request.content)["model"]
        if model == show_fails_for:
            return httpx.Response(500, json={"error": "boom"})
        if model == "llava:7b":
            return httpx.Response(200, json={
                "capabilities": ["completion", "vision"],
                "model_info": {"llava.context_length": 32768, "llava.block_count": 32},
            })
        return httpx.Response(200, json={
            "capabilities": ["completion", "tools"],
            "model_info": {"qwen3.context_length": 262144, "general.parameter_count": 8},
        })

    return handle


def test_merges_the_tag_list_with_per_model_details() -> None:
    seen: list[str] = []
    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(_handler(seen))))

    models = client.fetch("", base_url="http://localhost:11434")

    assert seen[0] == "GET http://localhost:11434/api/tags"
    assert [item["id"] for item in models] == ["qwen3:8b", "llava:7b"]
    assert models[0]["context_length"] == 262144
    assert models[0]["capabilities"] == ["completion", "tools"]
    assert models[1]["context_length"] == 32768
    assert models[1]["capabilities"] == ["completion", "vision"]


def test_sends_a_bearer_token_only_when_a_cloud_key_is_configured() -> None:
    headers: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        headers.append(dict(request.headers))
        return httpx.Response(200, json={"models": []})

    transport = httpx.MockTransport(handle)
    OllamaCatalogClient(client=httpx.Client(transport=transport)).fetch("", base_url="http://localhost:11434")
    assert "authorization" not in headers[0]

    OllamaCatalogClient(client=httpx.Client(transport=transport)).fetch("cloud-secret", base_url="https://ollama.com")
    assert headers[1]["authorization"] == "Bearer cloud-secret"


def test_cloud_access_probe_uses_an_authenticated_chat_request() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"model": "qwen3:8b", "message": {"content": "ok"}, "done": True})
        return httpx.Response(500, json={"error": "unexpected endpoint"})

    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handle)))

    client.verify_cloud_access("cloud-secret", base_url="https://ollama.com", model="qwen3:8b")

    assert requests[0].url == "https://ollama.com/api/chat"
    assert requests[0].headers["authorization"] == "Bearer cloud-secret"
    assert requests[0].read() is not None
    assert requests[0].content
    assert requests[0].method == "POST"
    assert b'"model":"qwen3:8b"' in requests[0].content
    assert b'"stream":false' in requests[0].content


def test_a_failed_detail_lookup_degrades_only_that_model() -> None:
    """One unreadable model must not cost the user the whole catalog refresh."""
    seen: list[str] = []
    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(_handler(seen, show_fails_for="llava:7b"))))

    models = client.fetch("", base_url="http://localhost:11434")

    assert [item["id"] for item in models] == ["qwen3:8b", "llava:7b"]
    assert models[1]["context_length"] is None
    assert models[1]["capabilities"] == []


def test_connection_failure_is_a_sanitized_catalog_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handle)))

    with pytest.raises(RuntimeError) as failure:
        client.fetch("cloud-secret", base_url="https://ollama.com")

    assert "cloud-secret" not in str(failure.value)
    assert "cloud-secret" not in repr(client)


def test_cloud_authentication_failure_is_distinguished_from_transport_failure() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handle)))

    with pytest.raises(OllamaCloudAuthenticationError):
        client.verify_cloud_access("cloud-secret", base_url="https://ollama.com", model="qwen3:8b")


def test_a_missing_cloud_key_is_an_authentication_error_not_a_generic_failure() -> None:
    """A blank key must surface the same way a rejected key does.

    ``test_connection`` only turns ``OllamaCloudAuthenticationError`` into the
    422 "credentials rejected" response; any other ``RuntimeError`` becomes an
    unhandled 500. A missing key is a credential problem, not a transport
    one, so it must raise the same exception type as a rejected key.
    """
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made without a key")

    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handle)))

    with pytest.raises(OllamaCloudAuthenticationError):
        client.verify_cloud_access("", base_url="https://ollama.com", model="qwen3:8b")


def test_production_composition_registers_the_ollama_upstream() -> None:
    """A provider absent from the composed upstreams can never refresh."""
    import inspect

    from agentos.bootstrap import production

    source = inspect.getsource(production)
    assert "OllamaCatalogClient" in source
    assert '"ollama": OllamaCatalogClient()' in source
