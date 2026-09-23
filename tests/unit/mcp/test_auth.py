import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import create_engine

from agentos.mcp.auth import McpOAuth, McpOAuthCallbackError, McpOAuthUnreachable, McpOAuthUnsupported
from agentos.mcp.oauth_records import McpOAuthRecords
from agentos.mcp.service import McpServerService, McpServiceError
from agentos.oauth.token_store import OAuthTokenStore
from agentos.persistence.postgres.schema import metadata

MCP_URL = "https://mcp.example.com/mcp"
REDIRECT = "http://127.0.0.1:49200/v1/mcp/oauth/callback"


class FakeAuthServer:
    """One MockTransport playing both the MCP server and its authorization server."""

    def __init__(self) -> None:
        self.registrations: list[dict] = []
        self.token_requests: list[dict] = []
        self.revocations: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == MCP_URL:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'})
        if url == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP_URL, "authorization_servers": ["https://auth.example.com/"],
                                             "scopes_supported": ["content:admin"]})
        if url == "https://auth.example.com/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "issuer": "https://auth.example.com/", "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token", "registration_endpoint": "https://auth.example.com/register",
                "revocation_endpoint": "https://auth.example.com/revoke", "code_challenge_methods_supported": ["S256"],
            })
        if url == "https://auth.example.com/register":
            body = json.loads(request.content)
            self.registrations.append(body)
            return httpx.Response(201, json={"client_id": f"c-{len(self.registrations)}", "token_endpoint_auth_method": "none"})
        if url == "https://auth.example.com/token":
            self.token_requests.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
        if url == "https://auth.example.com/revoke":
            self.revocations.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def _skip_dns(monkeypatch):
    monkeypatch.setattr("agentos.oauth.netpolicy._public_url", lambda url, resolve_dns=False: url)


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    service = McpServerService(engine)
    server_id = service.propose({"user_id": "u1", "display_name": "Auryly", "transport": "http", "url": MCP_URL})["server_id"]
    with engine.begin() as connection:
        from sqlalchemy import update
        from agentos.persistence.postgres.schema import mcp_servers
        connection.execute(update(mcp_servers).values(auth_kind="oauth", state_reason="motivo antigo"))
    fake = FakeAuthServer()
    oauth = McpOAuth(engine, http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return engine, oauth, fake, server_id


def test_start_registers_and_builds_the_authorization_url(world):
    engine, oauth, fake, server_id = world
    started = oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)

    query = parse_qs(urlsplit(started.authorization_url).query)
    assert started.authorization_url.startswith("https://auth.example.com/authorize?")
    assert query["client_id"] == ["c-1"]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["resource"] == [MCP_URL]
    assert query["scope"] == ["content:admin"]
    assert fake.registrations[0]["redirect_uris"] == [REDIRECT]
    assert McpOAuthRecords(engine).server("u1", server_id)["state_reason"] == ""


def test_start_reuses_the_registration_for_the_same_redirect(world):
    _engine, oauth, fake, server_id = world
    oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    assert len(fake.registrations) == 1


def test_a_new_port_means_a_new_registration(world):
    _engine, oauth, fake, server_id = world
    oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    started = oauth.start(user_id="u1", server_id=server_id, redirect_uri="http://127.0.0.1:49201/v1/mcp/oauth/callback")
    assert len(fake.registrations) == 2
    assert parse_qs(urlsplit(started.authorization_url).query)["client_id"] == ["c-2"]


def test_complete_exchanges_the_code_and_stores_the_tokens(world):
    engine, oauth, fake, server_id = world
    started = oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]

    assert oauth.complete(user_id="u1", state=state, code="code-1", error="") == server_id
    assert fake.token_requests[0]["code"] == "code-1"
    assert fake.token_requests[0]["resource"] == MCP_URL
    assert OAuthTokenStore(engine).get(user_id="u1", provider_id=server_id).access_token == "at"


def test_a_state_cannot_be_used_twice(world):
    _engine, oauth, _fake, server_id = world
    started = oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    oauth.complete(user_id="u1", state=state, code="code-1", error="")
    with pytest.raises(McpOAuthCallbackError):
        oauth.complete(user_id="u1", state=state, code="code-1", error="")


def test_an_unknown_state_changes_nothing(world):
    engine, oauth, _fake, server_id = world
    with pytest.raises(McpOAuthCallbackError):
        oauth.complete(user_id="u1", state="nope", code="c", error="")
    assert McpOAuthRecords(engine).server("u1", server_id)["state_reason"] == "motivo antigo"


def test_a_denied_consent_records_the_reason(world):
    engine, oauth, _fake, server_id = world
    started = oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    with pytest.raises(McpOAuthCallbackError, match="recusada"):
        oauth.complete(user_id="u1", state=state, code="", error="access_denied")
    assert McpOAuthRecords(engine).server("u1", server_id)["state_reason"] == "Autorização recusada"


def test_a_server_without_oauth_metadata_is_unsupported(world):
    engine, _oauth, _fake, server_id = world
    oauth = McpOAuth(engine, http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(McpOAuthUnsupported):
        oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    assert McpOAuthRecords(engine).server("u1", server_id)["state_reason"] != ""


def test_an_unreachable_server_is_reported_as_such(world):
    engine, _oauth, _fake, server_id = world

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    oauth = McpOAuth(engine, http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(McpOAuthUnreachable):
        oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)


def test_a_server_that_is_not_oauth_cannot_start_a_sign_in(world):
    engine, oauth, _fake, _server_id = world
    other = McpServerService(engine).propose({"user_id": "u1", "display_name": "Other", "transport": "http",
                                              "url": "https://other.example.com/mcp", "secret_names": ["token"]})
    with pytest.raises(McpServiceError):
        oauth.start(user_id="u1", server_id=other["server_id"], redirect_uri=REDIRECT)


def test_revoke_is_best_effort(world):
    engine, oauth, fake, server_id = world
    started = oauth.start(user_id="u1", server_id=server_id, redirect_uri=REDIRECT)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    oauth.complete(user_id="u1", state=state, code="code-1", error="")

    oauth.revoke(user_id="u1", server_id=server_id)
    assert fake.revocations == [{"token": "rt", "token_type_hint": "refresh_token", "client_id": "c-1"}]

    broken = McpOAuth(engine, http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    broken.revoke(user_id="u1", server_id=server_id)  # must not raise
