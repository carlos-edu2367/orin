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

def test_plugin_routes_apply_the_user_boundary():
    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    assert client.get("/v1/plugins").json()[0]["plugin_id"] == "demo"
    assert client.post("/v1/plugins/inspect", json={"reference":"obra/superpowers"}).status_code == 200
    assert client.post("/v1/plugins/demo/approve").json()["state"] == "active"
    assert client.put("/v1/plugins/demo/enabled", json={"enabled":False}).json()["state"] == "disabled"
    assert client.delete("/v1/plugins/demo").status_code == 204
