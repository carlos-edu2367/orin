from fastapi.testclient import TestClient
from agentos.api.gateway import ApiServices, create_app

class Security:
    requires_loopback_client = False
    def authenticate(self, **kwargs):
        from agentos.api.security import AuthenticatedPrincipal
        return AuthenticatedPrincipal(user_id="u1", credential_ref="c", scopes=frozenset())
    def validate_csrf(self, *args): pass
    def check_rate_limit(self, *args, **kwargs): pass
    def authorize(self, *args, **kwargs): pass

class Plugins:
    def list(self, user_id): return [{"plugin_id":"demo"}]
    def inspect(self, **kwargs): return {"plugin_id":"demo","state":"pending_approval"}
    def approve(self, **kwargs): return {"plugin_id":"demo","state":"active"}
    def set_enabled(self, **kwargs): return {"plugin_id":"demo","state":"disabled"}
    def remove(self, **kwargs): return {"removed":True}
    def list_marketplaces(self, user_id): return []
    def add_marketplace(self, **kwargs): return {"name":"community"}
    def discover_library(self, *, refresh=False, query=None): return {"entries": [], "web_search_available": refresh, "query_seen": query}
    def infer_mcp_launch(self, *, source_url): return {"display_name": "demo-mcp", "transport": "stdio", "command": "npx", "args": ["-y", "demo-mcp"], "url": None, "secret_names": [], "confidence": "structured", "source_url_seen": source_url}
    def list_commands(self, user_id): return [{"command_id": "demo:daily", "slug": "daily", "plugin_id": "demo", "description": "d", "argument_hint": "", "qualified": False}]


def test_commands_route_returns_the_active_commands():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    response = client.get("/v1/plugins/commands")
    assert response.status_code == 200
    assert response.json() == [{
        "command_id": "demo:daily", "slug": "daily", "plugin_id": "demo",
        "description": "d", "argument_hint": "", "qualified": False,
    }]

def test_plugin_routes_apply_the_user_boundary():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    assert client.get("/v1/plugins").json()[0]["plugin_id"] == "demo"
    assert client.post("/v1/plugins/inspect", json={"reference":"obra/superpowers"}).status_code == 200
    assert client.post("/v1/plugins/demo/approve").json()["state"] == "active"
    assert client.put("/v1/plugins/demo/enabled", json={"enabled":False}).json()["state"] == "disabled"
    assert client.delete("/v1/plugins/demo").status_code == 204

def test_plugin_library_route_forwards_the_refresh_flag():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    assert client.get("/v1/plugins/library").json()["web_search_available"] is False
    assert client.get("/v1/plugins/library?refresh=true").json()["web_search_available"] is True

def test_plugin_library_route_forwards_the_query_and_trims_blanks():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    assert client.get("/v1/plugins/library?q=obsidian").json()["query_seen"] == "obsidian"
    assert client.get("/v1/plugins/library?q=  ").json()["query_seen"] is None
    assert client.get("/v1/plugins/library").json()["query_seen"] is None

class RejectingPlugins(Plugins):
    def inspect(self, **kwargs):
        from agentos.plugins.service import PluginServiceError
        raise PluginServiceError("plugin package has no valid manifest", code="plugin_no_manifest")

def test_plugin_inspect_route_surfaces_the_no_manifest_code():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=RejectingPlugins())))
    response = client.post("/v1/plugins/inspect", json={"reference": "acme/no-manifest"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plugin_no_manifest"

def test_infer_mcp_route_forwards_the_source_url():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    response = client.post("/v1/plugins/library/infer-mcp", json={"source_url": "https://github.com/acme/demo-mcp.git"})
    assert response.status_code == 200
    body = response.json()
    assert body["command"] == "npx"
    assert body["source_url_seen"] == "https://github.com/acme/demo-mcp.git"
