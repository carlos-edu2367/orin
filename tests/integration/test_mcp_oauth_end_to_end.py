"""Approve → 401 → sign in → callback → active → tool call → refresh → revoked → error → reconnect."""
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.mcp.auth import McpOAuth
from agentos.mcp.service import McpServerService
from agentos.mcp.toolset import McpToolProvider
from agentos.mcp.transport_http import HttpTransport
from agentos.persistence.postgres.schema import metadata, oauth_tokens

MCP_URL = "https://mcp.example.com/mcp"


class FakeRemote:
    """An MCP server behind OAuth, with refresh-token rotation."""

    def __init__(self) -> None:
        self.valid_access: set[str] = set()
        self.valid_refresh: set[str] = set()
        self.issued = 0
        self.revoked = False

    def _issue(self) -> dict:
        self.issued += 1
        access, refresh = f"at-{self.issued}", f"rt-{self.issued}"
        self.valid_access = {access}
        self.valid_refresh = {refresh}
        return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == MCP_URL:
            token = request.headers.get("authorization", "")[7:]
            if token not in self.valid_access:
                return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'})
            frame = json.loads(request.content)
            if "id" not in frame:
                return httpx.Response(202)
            results = {
                "initialize": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}, "capabilities": {}},
                "tools/list": {"tools": [{"name": "list_tracks", "description": "Lista trilhas", "inputSchema": {"type": "object"}}]},
                "tools/call": {"content": [{"type": "text", "text": "3 trilhas"}]},
            }
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": frame["id"], "result": results[frame["method"]]})
        if url == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP_URL, "authorization_servers": ["https://auth.example.com/"]})
        if url == "https://auth.example.com/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "issuer": "https://auth.example.com/", "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token", "registration_endpoint": "https://auth.example.com/register",
                "code_challenge_methods_supported": ["S256"],
            })
        if url == "https://auth.example.com/register":
            return httpx.Response(201, json={"client_id": "c-1", "token_endpoint_auth_method": "none"})
        if url == "https://auth.example.com/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert form["resource"] == MCP_URL
            if form["grant_type"] == "refresh_token":
                if self.revoked or form["refresh_token"] not in self.valid_refresh:
                    return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=self._issue())
        return httpx.Response(404)


@pytest.fixture()
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    monkeypatch.setattr("agentos.oauth.netpolicy._public_url", lambda url, resolve_dns=False: url)
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    remote = FakeRemote()

    def mocked_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(remote), follow_redirects=False)

    def open_mocked(self) -> None:
        # The MCP transport builds its own client; point it at the fake remote.
        if self._client is None:
            self._client = mocked_client()

    monkeypatch.setattr(HttpTransport, "open", open_mocked)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    service = McpServerService(engine)
    oauth = McpOAuth(engine, http_client_factory=mocked_client)
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    api = TestClient(create_app(ApiServices(security=security, mcp=service, mcp_oauth=oauth)), base_url="http://127.0.0.1:49200")
    return engine, service, oauth, remote, api


def _sign_in(api: TestClient, server_id: str) -> None:
    started = api.post(f"/v1/mcp/servers/{server_id}/oauth/start", headers={"Authorization": "Bearer pat", "Idempotency-Key": uuid4().hex})
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    page = api.get(f"/v1/mcp/oauth/callback?state={state}&code=granted", headers={"Authorization": "Bearer pat"})
    assert page.status_code == 200, page.text


def test_the_whole_sign_in_life_cycle(world):
    engine, service, oauth, remote, api = world
    headers = {"Authorization": "Bearer pat", "Idempotency-Key": "k1"}
    server_id = api.post("/v1/mcp/servers", json={"display_name": "Auryly", "transport": "http", "url": MCP_URL},
                         headers=headers).json()["server_id"]

    approve = api.post(f"/v1/mcp/servers/{server_id}/approve", json={"secrets": {}}, headers={**headers, "Idempotency-Key": "k2"})
    assert approve.status_code == 409

    _sign_in(api, server_id)
    assert service.get("user-1", server_id)["state"] == "active"

    bundles = service.active_servers("user-1")
    provider = McpToolProvider(bundles, oauth=oauth)
    outcome = provider.definitions()[0].handler()
    assert outcome.status == "succeeded" and "3 trilhas" in outcome.content
    provider.close()

    # The access token is about to expire: the next call refreshes with rotation.
    with engine.begin() as connection:
        connection.execute(update(oauth_tokens).values(expires_at=oauth_tokens.c.updated_at))
    provider = McpToolProvider(service.active_servers("user-1"), oauth=oauth)
    assert provider.definitions()[0].handler().status == "succeeded"
    provider.close()
    assert remote.issued == 2

    # The grant is revoked on the server: the next refresh fails for good.
    remote.revoked = True
    remote.valid_access = set()
    provider = McpToolProvider(service.active_servers("user-1"), oauth=oauth)
    outcome = provider.definitions()[0].handler()
    provider.close()
    assert outcome.error_code == "MCP_REAUTH_REQUIRED"
    assert service.get("user-1", server_id)["state"] == "error"

    # Reconectar.
    remote.revoked = False
    _sign_in(api, server_id)
    assert service.get("user-1", server_id)["state"] == "active"
