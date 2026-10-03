import pytest
from fastapi.testclient import TestClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode

HEADERS = {"Authorization": "Bearer pat", "Idempotency-Key": "k-1"}


@pytest.fixture()
def api(tmp_path):
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    services = ApiServices(security=security, workspace_root=tmp_path, capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER))
    return TestClient(create_app(services))


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/v1/conversations/chat_1/files/notes.md/open"),
    ("post", "/v1/installation/update"),
    ("delete", "/v1/installation/versions/0.3.0"),
    ("post", "/v1/providers/omniroute/install"),
    ("get", "/v1/providers/omniroute/install"),
    ("get", "/v1/providers/omniroute/runtime"),
    ("put", "/v1/providers/omniroute/runtime"),
    ("post", "/v1/providers/omniroute/runtime/actions"),
    ("post", "/v1/providers/omniroute/test"),
])
def test_desktop_only_routes_are_closed_in_server_mode(api, method, path):
    kwargs = {"json": {}} if method in {"post", "put"} else {}
    response = getattr(api, method)(path, headers=HEADERS, **kwargs)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "capability_unavailable"


def test_the_native_folder_dialog_never_opens_in_server_mode(api, monkeypatch):
    def explode():
        raise AssertionError("the native dialog must not open on a server")
    monkeypatch.setattr("agentos.api.gateway.choose_folder", explode)
    response = api.post("/v1/workspaces/inspect", json={"path": None}, headers=HEADERS)
    assert response.json() == {"dialog_unavailable": True}
