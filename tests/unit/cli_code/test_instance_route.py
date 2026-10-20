"""W6 Task 3: GET /v1/instance tells `orin code` which Orin it is talking to."""
from __future__ import annotations

from fastapi.testclient import TestClient

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.version import __version__


def _client(**services) -> TestClient:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), **services))
    return TestClient(app, client=("127.0.0.1", 40000))


def test_a_local_orin_describes_itself_and_the_credential(monkeypatch) -> None:
    monkeypatch.delenv("ORIN_MODE", raising=False)
    body = _client().get("/v1/instance", headers={"Authorization": "Bearer tok"}).json()
    assert body == {
        "version": __version__, "mode": "local",
        "capabilities": InstanceCapabilities.for_mode(RuntimeMode.LOCAL).as_dict(), "credential": "cli_token",
    }
    assert _client().get("/v1/instance").json()["credential"] == "loopback"


def test_a_shared_orin_says_so(monkeypatch) -> None:
    monkeypatch.setenv("ORIN_MODE", "server")
    body = _client(capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER)).get("/v1/instance").json()
    assert body["mode"] == "server" and body["capabilities"]["host_folders"] is False


def test_a_wrong_token_is_refused() -> None:
    assert _client().get("/v1/instance", headers={"Authorization": "Bearer nope"}).status_code == 401
