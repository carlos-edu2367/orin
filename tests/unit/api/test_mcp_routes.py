from datetime import UTC, datetime

from fastapi.testclient import TestClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.mcp.auth import McpOAuthCallbackError, McpOAuthUnsupported, SignInStart
from agentos.mcp.service import McpAuthorizationRequired, McpConnectionFailed, McpServerNotFound


class FakeMcp:
    def __init__(self) -> None:
        self.proposed: list[dict] = []
        self.approved: list[dict] = []
        self.enabled_calls: list[tuple] = []
        self.removed: list[str] = []

    def list(self, user_id):
        assert user_id == "user-1"
        return [{"server_id": "s1", "slug": "github", "display_name": "GitHub", "transport": "stdio",
                 "command": "npx", "args": ["-y", "server-github"], "url": None,
                 "secret_names": ["GITHUB_PERSONAL_ACCESS_TOKEN"], "catalog_id": "github",
                 "state": "active", "state_reason": "", "protocol_version": "2025-06-18", "tool_count": 3}]

    def get(self, user_id, server_id):
        if server_id != "s1":
            raise McpServerNotFound(f"no MCP server '{server_id}' for this user")
        return self.list(user_id)[0]

    def propose(self, command):
        assert command["user_id"] == "user-1"
        self.proposed.append(dict(command))
        return {"server_id": "s2", "slug": "notion", "display_name": command["display_name"],
                "transport": command["transport"], "command": command.get("command"),
                "args": command.get("args", []), "url": command.get("url"),
                "secret_names": command.get("secret_names", []), "catalog_id": command.get("catalog_id"),
                "state": "pending_approval", "state_reason": "", "protocol_version": "", "tool_count": 0}

    def approve(self, *, user_id, server_id, secrets, connect):
        assert user_id == "user-1"
        self.approved.append({"server_id": server_id, "secrets": dict(secrets)})
        if server_id == "bad":
            raise McpConnectionFailed(f"could not connect to '{server_id}': token rejected")
        return {**self.get(user_id, "s1"), "server_id": server_id, "state": "active", "tool_count": 3}

    def set_enabled(self, user_id, server_id, *, enabled):
        self.enabled_calls.append((server_id, enabled))
        return {**self.get(user_id, "s1"), "state": "active" if enabled else "disabled"}

    def set_tool_enabled(self, user_id, server_id, tool_name, *, enabled):
        self.enabled_calls.append((server_id, tool_name, enabled))
        return self.get(user_id, "s1")

    def remove(self, user_id, server_id):
        if server_id != "s1":
            raise McpServerNotFound(f"no MCP server '{server_id}' for this user")
        self.removed.append(server_id)

    def test(self, user_id, slug, connect):
        assert user_id == "user-1"
        assert slug == "github"  # resolved from the server_id in the URL by the route
        return {"connected": True, "protocol_version": "2025-06-18", "tools": ["search"], "error": None}


def _client(mcp: FakeMcp | None = None) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    return TestClient(create_app(ApiServices(security=security, mcp=mcp if mcp is not None else FakeMcp())))


def _headers(key: str = "mcp-1") -> dict[str, str]:
    return {"Authorization": "Bearer pat", "Idempotency-Key": key}


def test_get_catalog_returns_the_curated_entries() -> None:
    client = _client()
    response = client.get("/v1/mcp/catalog?query=github", headers=_headers())
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries and entries[0]["catalog_id"] == "github"


def test_get_servers_never_returns_ciphertext_or_secret_values() -> None:
    client = _client()
    response = client.get("/v1/mcp/servers", headers=_headers())
    assert response.status_code == 200
    body = response.text
    assert "secrets_ciphertext" not in body
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in body  # names are fine — this is not a value


def test_post_servers_creates_a_pending_server() -> None:
    mcp = FakeMcp()
    client = _client(mcp)
    response = client.post("/v1/mcp/servers", headers=_headers(), json={
        "display_name": "Notion", "transport": "http", "url": "https://mcp.notion.com/mcp", "secret_names": [],
    })
    assert response.status_code == 201
    assert response.json()["state"] == "pending_approval"
    assert mcp.proposed[0]["display_name"] == "Notion"


def test_post_approve_activates_and_returns_the_tool_count() -> None:
    mcp = FakeMcp()
    client = _client(mcp)
    response = client.post("/v1/mcp/servers/s1/approve", headers=_headers(),
                           json={"secrets": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}})
    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["tool_count"] == 3
    assert "ghp_x" not in response.text


def test_post_approve_with_a_bad_credential_returns_502_and_keeps_it_pending() -> None:
    client = _client()
    response = client.post("/v1/mcp/servers/bad/approve", headers=_headers(), json={"secrets": {"TOKEN": "x"}})
    assert response.status_code == 502


def test_get_missing_server_returns_404() -> None:
    client = _client()
    response = client.get("/v1/mcp/servers/nope", headers=_headers())
    assert response.status_code == 404


def test_put_enabled_toggles_the_server() -> None:
    mcp = FakeMcp()
    client = _client(mcp)
    response = client.put("/v1/mcp/servers/s1/enabled", headers=_headers(), json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert mcp.enabled_calls == [("s1", False)]


def test_put_tool_enabled_toggles_one_tool() -> None:
    mcp = FakeMcp()
    client = _client(mcp)
    response = client.put("/v1/mcp/servers/s1/tools/search/enabled", headers=_headers(), json={"enabled": False})
    assert response.status_code == 200
    assert mcp.enabled_calls == [("s1", "search", False)]


def test_post_test_reports_connectivity() -> None:
    client = _client()
    response = client.post("/v1/mcp/servers/s1/test", headers=_headers())
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["tools"] == ["search"]


def test_delete_removes_the_server() -> None:
    mcp = FakeMcp()
    client = _client(mcp)
    response = client.delete("/v1/mcp/servers/s1", headers=_headers())
    assert response.status_code == 204
    assert mcp.removed == ["s1"]


def test_every_route_is_rate_limited_and_requires_the_loopback_principal() -> None:
    client = _client()
    response = client.get("/v1/mcp/servers")
    assert response.status_code == 401


class FakeOAuth:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.completed: list[dict] = []
        self.cancelled: list[str] = []
        self.revoked: list[str] = []

    def start(self, *, user_id, server_id, redirect_uri):
        self.started.append({"server_id": server_id, "redirect_uri": redirect_uri})
        if server_id == "no-oauth":
            raise McpOAuthUnsupported("no metadata")
        return SignInStart("https://auth.example.com/authorize?state=abc", datetime(2026, 9, 23, 12, 0, tzinfo=UTC))

    def complete(self, *, user_id, state, code, error):
        self.completed.append({"state": state, "code": code, "error": error})
        if state != "good":
            raise McpOAuthCallbackError("Este link de autorização não é válido ou já expirou.")
        return "s1"

    def cancel(self, *, user_id, server_id):
        self.cancelled.append(server_id)

    def revoke(self, *, user_id, server_id):
        self.revoked.append(server_id)

    def token_source(self, config):
        return None


class OAuthAwareMcp(FakeMcp):
    def __init__(self) -> None:
        super().__init__()
        self.activated: list[str] = []

    def approve(self, *, user_id, server_id, secrets, connect):
        if server_id == "needs-login":
            raise McpAuthorizationRequired("asks you to sign in")
        return super().approve(user_id=user_id, server_id=server_id, secrets=secrets, connect=connect)

    def activate_after_authorization(self, user_id, server_id, connect):
        self.activated.append(server_id)
        return {**self.get(user_id, "s1"), "state": "active"}

    def get(self, user_id, server_id):
        base = super().get(user_id, "s1")
        return {**base, "server_id": server_id, "auth_kind": "oauth"}


def _oauth_client(mcp=None, oauth=None) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    return TestClient(create_app(ApiServices(security=security, mcp=mcp or OAuthAwareMcp(), mcp_oauth=oauth or FakeOAuth())),
                      base_url="http://127.0.0.1:49200")


def test_approve_answers_409_when_the_server_asks_for_sign_in():
    response = _oauth_client().post("/v1/mcp/servers/needs-login/approve", json={"secrets": {}}, headers=_headers())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mcp_authorization_required"


def test_start_builds_the_loopback_redirect_from_the_api_address():
    oauth = FakeOAuth()
    response = _oauth_client(oauth=oauth).post("/v1/mcp/servers/s1/oauth/start", headers=_headers())
    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith("https://auth.example.com/")
    assert oauth.started[0]["redirect_uri"] == "http://127.0.0.1:49200/v1/mcp/oauth/callback"


def test_start_refuses_a_non_loopback_api_address():
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, mcp=OAuthAwareMcp(), mcp_oauth=FakeOAuth())),
                        base_url="http://orin.example.com")
    assert client.post("/v1/mcp/servers/s1/oauth/start", headers=_headers()).status_code == 422


def test_start_maps_an_unsupported_server_to_422():
    response = _oauth_client().post("/v1/mcp/servers/no-oauth/oauth/start", headers=_headers())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mcp_oauth_unsupported"


def test_cancel_forgets_the_pending_sign_in():
    oauth = FakeOAuth()
    response = _oauth_client(oauth=oauth).post("/v1/mcp/servers/s1/oauth/cancel", headers=_headers())
    assert response.status_code == 204
    assert oauth.cancelled == ["s1"]


def test_the_callback_completes_activates_and_renders_a_page():
    mcp = OAuthAwareMcp()
    response = _oauth_client(mcp=mcp).get("/v1/mcp/oauth/callback?state=good&code=abc", headers={"Authorization": "Bearer pat"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Conectado" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert mcp.activated == ["s1"]


def test_a_replayed_or_unknown_callback_changes_nothing_and_escapes_input():
    mcp = OAuthAwareMcp()
    response = _oauth_client(mcp=mcp).get("/v1/mcp/oauth/callback?state=<script>&code=abc",
                                          headers={"Authorization": "Bearer pat"})
    assert response.status_code == 400
    assert "<script>" not in response.text
    assert mcp.activated == []


def test_removing_an_oauth_server_revokes_first():
    oauth = FakeOAuth()
    response = _oauth_client(oauth=oauth).delete("/v1/mcp/servers/s1", headers=_headers())
    assert response.status_code == 204
    assert oauth.revoked == ["s1"]
