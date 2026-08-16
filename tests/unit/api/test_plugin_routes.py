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
