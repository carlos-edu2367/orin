import httpx
import pytest

from agentos.agentic.agent_tools import _public_url as real_public_url
from agentos.oauth.discovery import (
    BearerChallenge,
    OAuthDiscoveryError,
    OAuthDiscoveryUnreachable,
    discover_authorization_server,
    discover_protected_resource,
    parse_www_authenticate,
)

RESOURCE = "https://mcp.example.com/mcp"
AS_METADATA = {
    "issuer": "https://auth.example.com/",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": "https://auth.example.com/token",
    "registration_endpoint": "https://auth.example.com/register",
    "revocation_endpoint": "https://auth.example.com/revoke",
    "code_challenge_methods_supported": ["S256"],
    "scopes_supported": ["content:admin"],
}


def _client(routes: dict[str, httpx.Response], seen: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_www_authenticate_reads_the_bearer_parameters():
    header = 'Bearer error="invalid_token", error_description="no token", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp", scope="a b"'
    challenge = parse_www_authenticate(header)
    assert challenge == BearerChallenge(
        resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp", scope="a b", error="invalid_token",
    )


def test_parse_www_authenticate_tolerates_absent_or_foreign_schemes():
    assert parse_www_authenticate(None) == BearerChallenge(None, None, None)
    assert parse_www_authenticate('Basic realm="x"') == BearerChallenge(None, None, None)


def test_the_well_known_suffix_is_inserted_between_host_and_path():
    seen: list[str] = []
    client = _client({
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": httpx.Response(
            200, json={"resource": RESOURCE, "authorization_servers": ["https://auth.example.com/"], "scopes_supported": ["content:admin"]}),
    }, seen)
    metadata = discover_protected_resource(RESOURCE, client=client)
    assert seen[0] == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    assert metadata.authorization_servers == ("https://auth.example.com/",)
    assert metadata.scopes_supported == ("content:admin",)


def test_the_root_document_is_the_fallback():
    client = _client({
        "https://mcp.example.com/.well-known/oauth-protected-resource": httpx.Response(
            200, json={"resource": RESOURCE, "authorization_servers": ["https://auth.example.com"]}),
    })
    assert discover_protected_resource(RESOURCE, client=client).resource == RESOURCE


def test_the_challenge_metadata_url_is_tried_first():
    seen: list[str] = []
    client = _client({
        "https://meta.example.com/prm": httpx.Response(
            200, json={"resource": RESOURCE, "authorization_servers": ["https://auth.example.com"]}),
    }, seen)
    discover_protected_resource(RESOURCE, challenge=BearerChallenge("https://meta.example.com/prm", None, None), client=client)
    assert seen == ["https://meta.example.com/prm"]


def test_a_challenge_pointing_at_a_private_address_is_skipped(monkeypatch):
    monkeypatch.setattr("agentos.oauth.netpolicy._public_url", real_public_url)
    seen: list[str] = []
    # Only the well-known URL on the (literal, public) server address answers.
    client = _client({
        "https://93.184.216.34/.well-known/oauth-protected-resource/mcp": httpx.Response(
            200, json={"resource": "https://93.184.216.34/mcp", "authorization_servers": ["https://auth.example.com"]}),
    }, seen)
    metadata = discover_protected_resource(
        "https://93.184.216.34/mcp", challenge=BearerChallenge("https://127.0.0.1/prm", None, None), client=client)
    assert "https://127.0.0.1/prm" not in seen
    assert metadata.resource == "https://93.184.216.34/mcp"


def test_metadata_describing_another_resource_is_refused():
    client = _client({
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": httpx.Response(
            200, json={"resource": "https://evil.example.com/mcp", "authorization_servers": ["https://auth.example.com"]}),
    })
    with pytest.raises(OAuthDiscoveryError, match="not"):
        discover_protected_resource(RESOURCE, client=client)


def test_metadata_without_an_authorization_server_is_refused():
    client = _client({
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": httpx.Response(
            200, json={"resource": RESOURCE, "authorization_servers": []}),
    })
    with pytest.raises(OAuthDiscoveryError):
        discover_protected_resource(RESOURCE, client=client)


def test_authorization_server_metadata_is_read_and_checked():
    client = _client({"https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(200, json=AS_METADATA)})
    metadata = discover_authorization_server("https://auth.example.com/", client=client)
    assert metadata.token_endpoint == "https://auth.example.com/token"
    assert metadata.registration_endpoint == "https://auth.example.com/register"
    assert metadata.revocation_endpoint == "https://auth.example.com/revoke"


def test_an_issuer_with_a_path_falls_back_to_openid_configuration():
    issuer = "https://auth.example.com/tenant"
    client = _client({
        "https://auth.example.com/tenant/.well-known/openid-configuration": httpx.Response(200, json={**AS_METADATA, "issuer": issuer}),
    })
    assert discover_authorization_server(issuer, client=client).issuer == issuer


def test_a_mismatched_issuer_is_refused():
    client = _client({"https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
        200, json={**AS_METADATA, "issuer": "https://other.example.com/"})})
    with pytest.raises(OAuthDiscoveryError, match="issuer"):
        discover_authorization_server("https://auth.example.com/", client=client)


def test_a_server_without_s256_is_refused():
    client = _client({"https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
        200, json={**AS_METADATA, "code_challenge_methods_supported": ["plain"]})})
    with pytest.raises(OAuthDiscoveryError, match="S256"):
        discover_authorization_server("https://auth.example.com/", client=client)


def test_a_non_https_endpoint_is_refused():
    client = _client({"https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
        200, json={**AS_METADATA, "token_endpoint": "http://auth.example.com/token"})})
    with pytest.raises(OAuthDiscoveryError, match="https"):
        discover_authorization_server("https://auth.example.com/", client=client)


def test_an_unreachable_server_raises_the_unreachable_subtype():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthDiscoveryUnreachable):
        discover_authorization_server("https://auth.example.com/", client=client)
