import httpx

from agentos.plugins.github_search import GithubRepositorySearchClient, SearchResult


def test_github_repository_search_client_parses_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/repositories"
        assert "q=topic%3Amcp-server" in str(request.url)
        return httpx.Response(200, json={"items": [
            {"full_name": "acme/mcp-thing", "html_url": "https://github.com/acme/mcp-thing", "description": "An MCP server"},
        ]})

    client = GithubRepositorySearchClient(httpx.Client(transport=httpx.MockTransport(handler)))
    results = client.search("topic:mcp-server", limit=5)
    assert results == [SearchResult("acme/mcp-thing", "https://github.com/acme/mcp-thing", "An MCP server")]


def test_github_repository_search_client_tolerates_a_malformed_response():
    client = GithubRepositorySearchClient(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))))
    assert client.search("topic:mcp-server") == []


def test_github_repository_search_client_skips_items_without_a_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"full_name": "no-url/example", "description": "d"}]})

    client = GithubRepositorySearchClient(httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.search("topic:mcp-server") == []


def test_github_repository_search_client_raises_on_http_error():
    client = GithubRepositorySearchClient(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403))))
    try:
        client.search("topic:mcp-server")
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected an httpx.HTTPStatusError on a non-2xx response")
