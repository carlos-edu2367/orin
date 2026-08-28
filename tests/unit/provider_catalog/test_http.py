from __future__ import annotations

import httpx

from agentos.provider_catalog.http import AnthropicModelCatalogClient, OpenAIModelCatalogClient


def test_openai_catalog_uses_bearer_auth_and_its_models_endpoint(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def get(url: str, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs["headers"]
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("agentos.provider_catalog.http.httpx.get", get)

    assert OpenAIModelCatalogClient().fetch("test-key") == [{"id": "gpt-test"}]
    assert seen == {"url": "https://api.openai.com/v1/models", "headers": {"Authorization": "Bearer test-key"}}


def test_anthropic_catalog_pages_with_the_documented_cursor_and_headers(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def get(url: str, **kwargs):
        calls.append({"url": url, "headers": kwargs["headers"], "params": kwargs["params"]})
        payload = (
            {"data": [{"id": "claude-first"}], "has_more": True, "last_id": "claude-first"}
            if len(calls) == 1
            else {"data": [{"id": "claude-last"}], "has_more": False, "last_id": "claude-last"}
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr("agentos.provider_catalog.http.httpx.get", get)

    assert AnthropicModelCatalogClient().fetch("test-key") == [{"id": "claude-first"}, {"id": "claude-last"}]
    assert calls[0] == {
        "url": "https://api.anthropic.com/v1/models",
        "headers": {"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        "params": {"limit": 1000},
    }
    assert calls[1]["params"] == {"limit": 1000, "after_id": "claude-first"}
