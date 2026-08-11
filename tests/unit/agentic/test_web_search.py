from __future__ import annotations

import httpx

from agentos.agentic.web_search import BraveSearchClient, SearchResult, search_client_from_environment


def _client(payload: dict, captured: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_maps_the_response_into_bounded_results() -> None:
    payload = {"web": {"results": [
        {"title": "Orin docs", "url": "https://example.test/a", "description": "how it works"},
        {"title": "Other", "url": "https://example.test/b", "description": "more"},
    ]}}

    results = BraveSearchClient("key", _client(payload)).search("orin", limit=1)

    assert results == [SearchResult("Orin docs", "https://example.test/a", "how it works")]


def test_the_api_key_travels_in_the_header_and_never_in_the_query() -> None:
    captured: list[httpx.Request] = []
    BraveSearchClient("secret-key", _client({"web": {"results": []}}, captured)).search("orin")

    assert captured[0].headers["x-subscription-token"] == "secret-key"
    assert "secret-key" not in str(captured[0].url)


def test_a_malformed_response_yields_no_results_instead_of_raising() -> None:
    assert BraveSearchClient("key", _client({"unexpected": True})).search("orin") == []


def test_no_client_is_built_without_a_configured_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENTOS_SEARCH_API_KEY", raising=False)

    assert search_client_from_environment() is None


def test_a_client_is_built_when_the_key_is_present(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_SEARCH_API_KEY", "abc")

    assert isinstance(search_client_from_environment(), BraveSearchClient)
