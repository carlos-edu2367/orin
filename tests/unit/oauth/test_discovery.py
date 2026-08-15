import httpx
import pytest

from agentos.oauth.discovery import OAuthDiscoveryError, discover_authorization_endpoints


def test_discovers_authorize_and_token_urls_via_the_two_hop_well_known_flow():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": "https://mcp.example.com", "authorization_servers": ["https://auth.example.com"]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            assert request.url.host == "auth.example.com"
            return httpx.Response(200, json={
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
            })
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    authorize_url, token_url = discover_authorization_endpoints("https://mcp.example.com", client=client)

    assert authorize_url == "https://auth.example.com/authorize"
    assert token_url == "https://auth.example.com/token"


def test_raises_when_the_resource_metadata_is_missing():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthDiscoveryError):
        discover_authorization_endpoints("https://mcp.example.com", client=client)


def test_raises_when_the_resource_metadata_names_no_authorization_server():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": "https://mcp.example.com", "authorization_servers": []})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthDiscoveryError):
        discover_authorization_endpoints("https://mcp.example.com", client=client)


def test_raises_when_the_authorization_server_metadata_is_missing_the_token_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": "https://mcp.example.com", "authorization_servers": ["https://auth.example.com"]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"authorization_endpoint": "https://auth.example.com/authorize"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthDiscoveryError):
        discover_authorization_endpoints("https://mcp.example.com", client=client)


def test_refuses_a_non_https_resource_url():
    with pytest.raises(OAuthDiscoveryError):
        discover_authorization_endpoints("http://mcp.example.com", client=httpx.Client())


def test_refuses_a_non_https_authorization_server_named_by_the_resource_metadata():
    # The resource metadata is what names the authorization server — if that hop
    # is (or becomes, via a MITM) plaintext http://, the second metadata fetch
    # travels unencrypted and its response (including the eventual authorize_url/
    # token_url) can be forged in transit. Reject before ever making that request.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": "https://mcp.example.com", "authorization_servers": ["http://auth.example.com"]})
        raise AssertionError("must not fetch authorization-server metadata over plaintext http")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthDiscoveryError):
        discover_authorization_endpoints("https://mcp.example.com", client=client)
