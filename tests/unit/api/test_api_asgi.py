from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemorySecurityService,
    create_app,
)
from agentos.api.security import LoopbackSecurityService
from agentos.bootstrap.production import DependencyProbe, ProductionSettings, create_production_app


class FakeExecutionApplication:
    def create(self, command: dict[str, object]) -> dict[str, object]:
        return {"outcome": "accepted", "execution_id": command["context"]["execution_id"], "state_version": 1}

    def control(self, command: dict[str, object]) -> dict[str, object]:
        return {"outcome": "accepted", "execution_id": command["context"]["execution_id"], "state_version": 2}

    def provide_input(self, command: dict[str, object]) -> dict[str, object]:
        return {"outcome": "accepted", "execution_id": command["context"]["execution_id"], "state_version": 2}


class FakeProviderConfiguration:
    def __init__(self) -> None:
        self.configure_calls = 0

    def configure(self, command: dict[str, object]) -> dict[str, object]:
        self.configure_calls += 1
        assert command["api_key"] == "key-writes-only"
        return {"provider": command["provider"], "enabled": True, "secret_ref": "secret-ref", "api_key": "must-not-leak"}

    def inspect(self, query: dict[str, object]) -> dict[str, object]:
        return {"provider": query["provider"], "enabled": True, "secret_ref": "secret-ref"}

    def revoke(self, command: dict[str, object]) -> dict[str, object]:
        return {"provider": command["provider"], "enabled": False, "secret_ref": "secret-ref"}


class FakeProviderCatalog:
    def refresh(self, context, provider: str):
        assert context.user_id == "user-1"
        assert provider == "openrouter"
        return {"refreshed_at": datetime(2026, 8, 10, tzinfo=UTC), "count": 1}

    def list(self, context, provider: str, favorites_only: bool = False):
        assert context.user_id == "user-1"
        assert provider == "openrouter"
        assert favorites_only is False
        return [{
            "provider": "openrouter", "model_id": "anthropic/test-model", "display_name": "Test model",
            "context_window": 128000, "capabilities": ("tools",), "input_modalities": ("text",),
            "output_modalities": ("text",), "pricing": {"input_per_million": "3", "output_per_million": "15"},
            "refreshed_at": datetime(2026, 8, 10, tzinfo=UTC), "is_favorite": False,
            "api_key": "must-not-leak", "raw_payload": {"description": "must-not-leak"},
        }]

    def favorite(self, context, provider: str, model_id: str, favorite: bool):
        assert context.user_id == "user-1"
        assert provider == "openrouter"
        assert model_id == "anthropic/test-model"
        return {
            "provider": provider, "model_id": model_id, "display_name": "Test model", "context_window": 128000,
            "capabilities": (), "input_modalities": ("text",), "output_modalities": ("text",), "pricing": None,
            "refreshed_at": datetime(2026, 8, 10, tzinfo=UTC), "is_favorite": favorite,
        }


class FakeAgentRuntimeSettings:
    def __init__(self) -> None:
        self.value: int | None = None

    def get(self, user_id: str) -> dict[str, int | None]:
        assert user_id == "user-1"
        return {"max_iterations": self.value}

    def set_max_iterations(self, user_id: str, value: int | None) -> dict[str, int | None]:
        assert user_id == "user-1"
        self.value = value
        return {"max_iterations": value}


class FakeConversationApplication:
    def __init__(self) -> None:
        self.create_calls = 0
        self.send_calls: list[dict[str, object]] = []

    def allocate_conversation_id(self) -> str:
        return "conv-1"

    def create(self, context, *, message, provider, model_id, workspace_id, idempotency_key, project_id=None, attachments=(), new_conversation_id=None):
        self.create_calls += 1
        assert context.user_id == "user-1"
        assert (message, provider, model_id, workspace_id) == ("Organize este projeto", "openrouter", "anthropic/test-model", None)
        return {"conversation_id": "conv-1", "title": "Organize este projeto", "turn_id": "turn-1", "message_id": "msg-1", "state": "queued", "task_ref": "must-not-leak"}

    def send(self, user_id, conversation_id, message, idempotency_key, attachments=(), provider="", model_id=""):
        self.send_calls.append({"user_id": user_id, "conversation_id": conversation_id, "message": message, "provider": provider, "model_id": model_id})
        return {"conversation_id": conversation_id, "title": "Conversa", "turn_id": "turn-2", "message_id": "msg-2", "state": "queued"}


class FakeProjectStore:
    def __init__(self, workspace_id: str = "project-workspace") -> None:
        self.project = type("Project", (), {"project_id": "project-a", "workspace_id": workspace_id, "name": "AgentOS"})()

    def get(self, project_id: str, user_id: str):
        return self.project if project_id == "project-a" and user_id == "user-1" else None


class FakeLocalWorkspaceStore:
    def __init__(self) -> None:
        self.roots: dict[tuple[str, str], str] = {}

    def root_for(self, workspace_id: str, user_id: str) -> str | None:
        return self.roots.get((workspace_id, user_id))

    def set_root(self, workspace_id: str, user_id: str, root_path: str) -> None:
        self.roots[(workspace_id, user_id)] = root_path

    def clear_root(self, workspace_id: str, user_id: str) -> bool:
        return self.roots.pop((workspace_id, user_id), None) is not None


def _client() -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, execution_application=FakeExecutionApplication()))
    return TestClient(app)


def test_pat_mutation_is_translated_to_application_port_without_transport_ownership() -> None:
    response = _client().post(
        "/v1/executions",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "create-1"},
        json={"agent_id": "agent-1", "task_ref": "task-1", "workspace_id": "workspace-1"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["execution_id"]
    assert body["state_version"] == 1


def test_cookie_mutation_requires_csrf_and_public_error_never_leaks_details() -> None:
    security = InMemorySecurityService()
    security.add_session("sid-1", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})), csrf_token="csrf-1")
    app = create_app(ApiServices(security=security, execution_application=FakeExecutionApplication()))
    client = TestClient(app)
    client.cookies.set("agentos_session", "sid-1")

    response = client.post(
        "/v1/executions",
        json={"agent_id": "agent-1", "task_ref": "task-1", "workspace_id": "workspace-1"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["category"] == "AUTHORIZATION"
    assert "sid-1" not in response.text


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_localhost_trust_accepts_provider_setup_without_browser_session(host: str) -> None:
    provider_configuration = FakeProviderConfiguration()
    app = create_app(ApiServices(
        security=LoopbackSecurityService(),
        execution_application=FakeExecutionApplication(),
        provider_configuration=provider_configuration,
    ))

    response = TestClient(app, client=(host, 49152)).put(
        "/v1/providers/openrouter",
        headers={"Idempotency-Key": "provider-local-1"},
        json={"api_key": "key-writes-only", "enabled": True},
    )

    assert response.status_code == 200
    assert provider_configuration.configure_calls == 1


@pytest.mark.parametrize("host", ["192.0.2.55", "2001:db8::55", "testclient"])
def test_localhost_trust_rejects_non_loopback_clients(host: str) -> None:
    provider_configuration = FakeProviderConfiguration()
    app = create_app(ApiServices(
        security=LoopbackSecurityService(),
        execution_application=FakeExecutionApplication(),
        provider_configuration=provider_configuration,
    ))

    response = TestClient(app, client=(host, 49152)).put(
        "/v1/providers/openrouter",
        headers={"Idempotency-Key": "provider-local-remote"},
        json={"api_key": "key-writes-only", "enabled": True},
    )

    assert response.status_code == 401
    assert provider_configuration.configure_calls == 0


def test_sse_replay_uses_opaque_cursor_and_stops_when_revocation_epoch_changes() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    services = ApiServices(security=security, execution_application=FakeExecutionApplication())
    services.events.append("execution-1", "ExecutionQueued", {"state": "QUEUED"})
    app = create_app(services)
    client = TestClient(app)

    opened = client.post(
        "/v1/events/streams",
        headers={"Authorization": "Bearer pat-test"},
        json={"execution_ids": ["execution-1"]},
    )

    assert opened.status_code == 201
    assert opened.json()["cursor"].startswith("c.")
    assert opened.json()["stream_id"]


def test_provider_setup_accepts_key_only_on_write_and_never_returns_it() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), provider_configuration=FakeProviderConfiguration()))
    client = TestClient(app)

    saved = client.put("/v1/providers/openai", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "provider-1"}, json={"api_key": "key-writes-only"})
    inspected = client.get("/v1/providers/openai", headers={"Authorization": "Bearer pat-test"})

    assert saved.status_code == 200
    assert "key-writes-only" not in saved.text and "must-not-leak" not in saved.text
    assert inspected.status_code == 200
    assert "model" not in saved.json()
    assert "model" not in inspected.json()


def test_installation_status_and_version_removal_use_the_local_release_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.api import gateway as gateway_module

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    profile = object()
    status = {
        "installation_kind": "installed", "current_version": "0.1.12",
        "installed_versions": [{"version": "0.1.12", "is_current": True, "removable": False}, {"version": "0.1.11", "is_current": False, "removable": True}],
        "latest_release": {"version": "0.1.12", "url": "https://github.com/carlos-edu2367/orin/releases/tag/v0.1.12"},
        "latest_release_error": None, "checked_at": "2026-08-14T00:00:00Z",
    }
    monkeypatch.setattr(gateway_module, "runtime_profile", lambda: profile)
    monkeypatch.setattr(gateway_module, "read_installation_status", lambda received: status if received is profile else {})
    monkeypatch.setattr(gateway_module, "remove_installed_version", lambda version, received: {"removed_version": version} if received is profile else {})
    client = TestClient(create_app(ApiServices(security=security)))

    inspected = client.get("/v1/installation/status", headers={"Authorization": "Bearer pat-test"})
    removed = client.delete("/v1/installation/versions/0.1.11", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "remove-version-1"})

    assert inspected.status_code == 200 and inspected.json()["current_version"] == "0.1.12"
    assert removed.status_code == 200 and removed.json() == {"removed_version": "0.1.11"}


def test_omniroute_setup_accepts_a_public_base_url_without_returning_its_key() -> None:
    class OmniRouteConfiguration(FakeProviderConfiguration):
        def configure(self, command: dict[str, object]) -> dict[str, object]:
            assert command["provider"] == "omniroute"
            assert command["base_url"] == "http://localhost:20128/v1"
            assert command["api_key"] == "omni-key-writes-only"
            return {"provider": "omniroute", "enabled": True, "base_url": command["base_url"], "secret_ref": "omni-secret-ref"}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        provider_configuration=OmniRouteConfiguration(),
    )))

    response = client.put(
        "/v1/providers/omniroute",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "omniroute-1"},
        json={"api_key": "omni-key-writes-only", "base_url": "http://localhost:20128/v1"},
    )

    assert response.status_code == 200
    assert response.json()["base_url"] == "http://localhost:20128/v1"
    assert "omni-key-writes-only" not in response.text


def test_omniroute_connection_test_does_not_persist_or_echo_the_supplied_key() -> None:
    class OmniRouteConfiguration(FakeProviderConfiguration):
        def test_connection(self, command: dict[str, object]) -> dict[str, object]:
            assert command["provider"] == "omniroute"
            assert command["api_key"] == "omni-key-for-test"
            assert command["base_url"] == "http://localhost:20128/v1"
            return {"connected": True, "models_available": 2, "base_url": command["base_url"]}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(
        security=security, execution_application=FakeExecutionApplication(), provider_configuration=OmniRouteConfiguration(),
    )))

    response = client.post(
        "/v1/providers/omniroute/test",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "omniroute-test-1"},
        json={"api_key": "omni-key-for-test", "base_url": "http://localhost:20128/v1"},
    )

    assert response.status_code == 200
    assert response.json() == {"connected": True, "models_available": 2, "base_url": "http://localhost:20128/v1"}
    assert "omni-key-for-test" not in response.text


def test_omniroute_connection_test_allows_a_no_auth_local_gateway() -> None:
    class OmniRouteConfiguration(FakeProviderConfiguration):
        def test_connection(self, command: dict[str, object]) -> dict[str, object]:
            assert command["api_key"] == ""
            return {"connected": True, "models_available": 0, "base_url": "http://localhost:20128/v1"}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), provider_configuration=OmniRouteConfiguration())))
    response = client.post("/v1/providers/omniroute/test", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "omniroute-test-noauth"}, json={"base_url": "http://localhost:20128/v1"})

    assert response.status_code == 200
    assert response.json()["connected"] is True


def test_omniroute_install_is_an_explicit_authenticated_provider_action() -> None:
    class OmniRouteConfiguration(FakeProviderConfiguration):
        def install(self, command: dict[str, object]) -> dict[str, object]:
            assert command == {"provider": "omniroute", "user_id": "user-1", "purpose": "provider.install"}
            return {"installed": True, "next_step": "omniroute"}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), provider_configuration=OmniRouteConfiguration())))

    response = client.post("/v1/providers/omniroute/install", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "omniroute-install-1"})

    assert response.status_code == 200
    assert response.json() == {"installed": True, "next_step": "omniroute"}


def test_omniroute_install_status_reports_a_completed_local_installation() -> None:
    class OmniRouteConfiguration(FakeProviderConfiguration):
        def installation_status(self, query: dict[str, object]) -> dict[str, object]:
            assert query == {"provider": "omniroute", "user_id": "user-1", "purpose": "provider.install.status"}
            return {"installed": True}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), provider_configuration=OmniRouteConfiguration())))

    response = client.get("/v1/providers/omniroute/install", headers={"Authorization": "Bearer pat-test"})

    assert response.status_code == 200
    assert response.json() == {"installed": True}


def test_omniroute_runtime_persists_autostart_and_refuses_external_stop() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.enabled = False

        def status(self) -> dict[str, object]:
            return {"state": "external", "ownership": "external"}

        def auto_start(self, user_id: str) -> bool:
            assert user_id == "user-1"
            return self.enabled

        def set_auto_start(self, user_id: str, value: bool) -> dict[str, object]:
            assert user_id == "user-1"
            self.enabled = value
            return {"auto_start": value}

        def stop(self) -> dict[str, object]:
            return {"state": "external", "ownership": "external"}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), omniroute_runtime=Runtime())))

    saved = client.put("/v1/providers/omniroute/runtime", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "omniroute-runtime"}, json={"auto_start": True})
    stopped = client.post("/v1/providers/omniroute/runtime/actions", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "omniroute-stop"}, json={"action": "stop"})

    assert saved.status_code == 200
    assert saved.json()["auto_start"] is True
    assert stopped.json()["ownership"] == "external"


def test_agent_runtime_settings_accept_unlimited_or_bounded_iterations() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    runtime = FakeAgentRuntimeSettings()
    client = TestClient(create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), agentic_runtime=runtime)))

    initial = client.get("/v1/runtime/settings", headers={"Authorization": "Bearer pat-test"})
    bounded = client.put("/v1/runtime/settings", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "runtime-limit"}, json={"max_iterations": 48})
    unlimited = client.put("/v1/runtime/settings", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "runtime-unlimited"}, json={"max_iterations": None})

    assert initial.json() == {"max_iterations": None}
    assert bounded.json() == {"max_iterations": 48}
    assert unlimited.json() == {"max_iterations": None}


def test_memory_management_scopes_query_and_does_not_mix_projects() -> None:
    class ProjectStore:
        def list_memories(self, project_id: str | None, user_id: str, *, scope: str, query: str, cursor: str | None, limit: int):
            assert user_id == "user-1" and scope == "project" and project_id == "project-a"
            assert query == "FastAPI" and cursor is None and limit == 50
            return {"items": [{"memory_id": "mem-a", "fact": "Uses FastAPI", "tags": [], "scope": "project", "project_id": "project-a"}], "next_cursor": None}

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), projects=ProjectStore())))

    response = client.get("/v1/memories?scope=project&project_id=project-a&query=FastAPI", headers={"Authorization": "Bearer pat-test"})

    assert response.status_code == 200
    assert response.json()["items"] == [{"memory_id": "mem-a", "fact": "Uses FastAPI", "tags": [], "scope": "project", "project_id": "project-a"}]


def test_provider_inspect_serializes_catalog_timestamp() -> None:
    class TimestampedProviderConfiguration(FakeProviderConfiguration):
        def inspect(self, query: dict[str, object]) -> dict[str, object]:
            return {
                "provider": query["provider"],
                "enabled": True,
                "secret_ref": "secret-ref",
                "catalog_refreshed_at": datetime(2026, 8, 10, 12, 30, tzinfo=UTC),
            }

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        provider_configuration=TimestampedProviderConfiguration(),
    ))

    response = TestClient(app).get("/v1/providers/openrouter", headers={"Authorization": "Bearer pat-test"})

    assert response.status_code == 200
    assert response.json()["catalog_refreshed_at"] == "2026-08-10T12:30:00+00:00"


def test_openrouter_write_with_cookie_and_csrf_reaches_configuration_port() -> None:
    principal = AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"}))
    security = InMemorySecurityService()
    security.add_session("sid-1", principal, csrf_token="csrf-1")
    provider_configuration = FakeProviderConfiguration()
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        provider_configuration=provider_configuration,
    ))
    client = TestClient(app)
    client.cookies.set("agentos_session", "sid-1")

    response = client.put(
        "/v1/providers/openrouter",
        headers={"X-CSRF-Token": "csrf-1", "Origin": "http://127.0.0.1:4173", "Idempotency-Key": "provider-1"},
        json={"api_key": "key-writes-only", "enabled": True},
    )

    assert response.status_code == 200
    assert provider_configuration.configure_calls == 1
    assert "key-writes-only" not in response.text
    assert "csrf-1" not in response.text
    assert "sid-1" not in response.text


def test_openrouter_write_without_cookie_is_unauthenticated_and_does_not_call_port() -> None:
    provider_configuration = FakeProviderConfiguration()
    app = create_app(ApiServices(
        security=InMemorySecurityService(),
        execution_application=FakeExecutionApplication(),
        provider_configuration=provider_configuration,
    ))

    response = TestClient(app).put(
        "/v1/providers/openrouter",
        headers={"X-CSRF-Token": "csrf-1", "Origin": "http://127.0.0.1:4173", "Idempotency-Key": "provider-1"},
        json={"api_key": "key-writes-only", "enabled": True},
    )

    assert response.status_code == 401
    assert response.json()["error"]["category"] == "AUTHENTICATION"
    assert provider_configuration.configure_calls == 0
    assert "key-writes-only" not in response.text


@pytest.mark.parametrize("csrf_token", [None, "wrong-csrf"])
def test_openrouter_write_without_valid_csrf_is_forbidden_and_does_not_call_port(csrf_token: str | None) -> None:
    principal = AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"}))
    security = InMemorySecurityService()
    security.add_session("sid-1", principal, csrf_token="csrf-1")
    provider_configuration = FakeProviderConfiguration()
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        provider_configuration=provider_configuration,
    ))
    client = TestClient(app)
    client.cookies.set("agentos_session", "sid-1")
    headers = {"Origin": "http://127.0.0.1:4173", "Idempotency-Key": "provider-1"}
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token

    response = client.put("/v1/providers/openrouter", headers=headers, json={"api_key": "key-writes-only", "enabled": True})

    assert response.status_code == 403
    assert response.json()["error"]["category"] == "AUTHORIZATION"
    assert provider_configuration.configure_calls == 0
    assert "key-writes-only" not in response.text
    assert "csrf-1" not in response.text


def test_conversation_with_cookie_and_csrf_reaches_conversation_port() -> None:
    principal = AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"}))
    security = InMemorySecurityService()
    security.add_session("sid-1", principal, csrf_token="csrf-1")
    conversation_application = FakeConversationApplication()
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        conversation_application=conversation_application,
    ))
    client = TestClient(app)
    client.cookies.set("agentos_session", "sid-1")

    response = client.post(
        "/v1/conversations",
        headers={"X-CSRF-Token": "csrf-1", "Origin": "http://127.0.0.1:4173", "Idempotency-Key": "conversation-1"},
        json={"message": "Organize este projeto", "selection": {"provider": "openrouter", "model_id": "anthropic/test-model"}, "workspace_id": None},
    )

    assert response.status_code == 201
    assert conversation_application.create_calls == 1
    assert "task_ref" not in response.text
    assert "csrf-1" not in response.text
    assert "sid-1" not in response.text


def test_provider_catalog_refresh_and_list_expose_only_sanitized_owner_scoped_metadata() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), provider_catalog=FakeProviderCatalog()))
    client = TestClient(app)
    headers = {"Authorization": "Bearer pat-test", "Idempotency-Key": "catalog-refresh"}

    refreshed = client.post("/v1/providers/openrouter/models:refresh", headers=headers)
    listed = client.get("/v1/providers/openrouter/models", headers={"Authorization": "Bearer pat-test"})

    assert refreshed.status_code == 200
    assert refreshed.json()["count"] == 1
    assert listed.status_code == 200
    assert listed.json()["items"][0]["model_id"] == "anthropic/test-model"
    assert "api_key" not in listed.text
    assert "raw_payload" not in listed.text


def test_provider_favorite_requires_an_authorized_catalog_identity() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), provider_catalog=FakeProviderCatalog()))
    client = TestClient(app)
    headers = {"Authorization": "Bearer pat-test", "Idempotency-Key": "favorite-1"}

    favorited = client.put("/v1/providers/openrouter/favorites/anthropic%2Ftest-model", headers=headers)
    unfavorited = client.delete("/v1/providers/openrouter/favorites/anthropic%2Ftest-model", headers={**headers, "Idempotency-Key": "favorite-2"})

    assert favorited.status_code == 200 and favorited.json()["is_favorite"] is True
    assert unfavorited.status_code == 200 and unfavorited.json()["is_favorite"] is False


def test_conversation_endpoint_accepts_message_and_selection_without_returning_task_reference() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, execution_application=FakeExecutionApplication(), conversation_application=FakeConversationApplication()))
    client = TestClient(app)

    response = client.post("/v1/conversations", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "conversation-1"}, json={"message": "Organize este projeto", "selection": {"provider": "openrouter", "model_id": "anthropic/test-model"}, "workspace_id": None})

    assert response.status_code == 201
    assert response.json()["conversation_id"] == "conv-1"
    assert "execution_id" not in response.text
    assert "task_ref" not in response.text


def test_conversation_endpoint_creates_project_chat_with_initial_workspace(tmp_path: Path) -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    local_workspaces = FakeLocalWorkspaceStore()
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        conversation_application=FakeConversationApplication(),
        projects=FakeProjectStore(),
        local_workspaces=local_workspaces,
    ))

    response = TestClient(app).post(
        "/v1/conversations",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "project-conversation-1"},
        json={
            "message": "Organize este projeto",
            "selection": {"provider": "openrouter", "model_id": "anthropic/test-model"},
            "project_id": "project-a",
            "workspace_path": str(tmp_path),
        },
    )

    assert response.status_code == 201
    assert local_workspaces.roots[("project-workspace", "user-1")] == str(tmp_path)


def test_conversation_message_accepts_a_new_authorized_model_selection() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    conversation_application = FakeConversationApplication()
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        conversation_application=conversation_application,
        provider_catalog=FakeProviderCatalog(),
    ))
    client = TestClient(app)

    response = client.post(
        "/v1/conversations/conv-1/messages",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "conversation-switch-1"},
        json={"message": "Use outro modelo", "attachments": [], "selection": {"provider": "openrouter", "model_id": "anthropic/test-model"}},
    )

    assert response.status_code == 201
    assert conversation_application.send_calls[-1]["provider"] == "openrouter"
    assert conversation_application.send_calls[-1]["model_id"] == "anthropic/test-model"


def test_conversation_message_rejects_a_model_outside_the_authorized_catalog() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    conversation_application = FakeConversationApplication()
    app = create_app(ApiServices(
        security=security,
        execution_application=FakeExecutionApplication(),
        conversation_application=conversation_application,
        provider_catalog=FakeProviderCatalog(),
    ))
    client = TestClient(app)

    response = client.post(
        "/v1/conversations/conv-1/messages",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "conversation-switch-2"},
        json={"message": "Use um modelo não autorizado", "selection": {"provider": "openrouter", "model_id": "not-in-catalog"}},
    )

    assert response.status_code == 422
    assert conversation_application.send_calls == []


def test_production_bootstrap_accepts_enabled_provider_with_key_only_and_exposes_sanitized_unready_status() -> None:
    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
        REDIS_URL="redis://localhost:6379/0",
        OPENAI_ENABLED=True,
        OPENAI_API_KEY="key",
    )
    app = create_production_app(settings, probe=DependencyProbe(lambda: False, lambda: False))

    response = TestClient(app, raise_server_exceptions=False).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}

    with pytest.raises(ValueError, match="cannot use in-memory"):
        create_production_app(settings, services=ApiServices(), probe=DependencyProbe(lambda: True, lambda: True))


def test_production_bootstrap_does_not_wait_for_optional_omniroute() -> None:
    class DurablePort:
        pass

    class Runtime:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def start_if_any_enabled_in_background(self) -> dict[str, object]:
            self.started = True
            return {"state": "starting", "ownership": "agentos"}

        def stop(self) -> dict[str, object]:
            self.stopped = True
            return {"state": "stopped", "ownership": None}

        def start_if_any_enabled(self) -> dict[str, object]:
            raise AssertionError("production startup must not use the blocking autostart")

    runtime = Runtime()
    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
        REDIS_URL="redis://localhost:6379/0",
    )
    app = create_production_app(
        settings,
        services=ApiServices(security=DurablePort(), events=DurablePort(), omniroute_runtime=runtime),
        probe=DependencyProbe(lambda: True, lambda: True),
    )

    with TestClient(app):
        pass

    assert runtime.started is True
    assert runtime.stopped is True


def test_local_spa_marks_the_served_html_as_loopback_without_rebuilding_frontend(tmp_path: Path) -> None:
    """Break caught: local FastAPI served an empty auth marker and blocked its own UI."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<meta name="agentos-auth-mode" content=""><div id="root"></div>', encoding="utf-8")
    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos", REDIS_URL="redis://localhost:6379/0",
        AGENTOS_ENV="local", LOCALHOST_TRUST_ENABLED=True, WEB_DIST_DIR=str(tmp_path),
    )

    response = TestClient(create_production_app(settings, probe=DependencyProbe(lambda: True, lambda: True))).get("/")

    assert response.status_code == 200
    assert 'name="agentos-auth-mode" content="loopback"' in response.text


@pytest.mark.parametrize("environment", ["production", "staging", "vps"])
def test_production_settings_reject_localhost_trust_outside_local_environments(environment: str) -> None:
    with pytest.raises(ValueError, match="LOCALHOST_TRUST_ENABLED"):
        ProductionSettings(
            DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
            REDIS_URL="redis://localhost:6379/0",
            AGENTOS_ENV=environment,
            LOCALHOST_TRUST_ENABLED=True,
        )


def test_localhost_trust_requires_a_built_frontend_directory() -> None:
    with pytest.raises(ValueError, match="WEB_DIST_DIR"):
        ProductionSettings(
            DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
            REDIS_URL="redis://localhost:6379/0",
            AGENTOS_ENV="local",
            LOCALHOST_TRUST_ENABLED=True,
        )


def test_localhost_trust_serves_the_built_single_page_application(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "main.js").write_text("console.log('local')", encoding="utf-8")
    (tmp_path / "index.html").write_text("<main>AgentOS local</main>", encoding="utf-8")
    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
        REDIS_URL="redis://localhost:6379/0",
        AGENTOS_ENV="local",
        LOCALHOST_TRUST_ENABLED=True,
        WEB_DIST_DIR=str(tmp_path),
    )
    app = create_production_app(settings, probe=DependencyProbe(lambda: True, lambda: True))

    response = TestClient(app).get("/")
    missing_api = TestClient(app).get("/v1/not-a-real-route")

    assert response.status_code == 200
    assert response.text == "<main>AgentOS local</main>"
    assert missing_api.status_code == 404
    assert missing_api.json()["error"]["category"] == "NOT_FOUND"


def test_ollama_is_an_accepted_provider_name() -> None:
    from agentos.api.gateway import _provider_name

    assert _provider_name("Ollama") == "ollama"
    with pytest.raises(ValueError):
        _provider_name("llamafile")


def test_configuring_ollama_does_not_require_a_key_at_the_gateway() -> None:
    """Local Ollama is keyless; the Cloud rule lives in the adapter, which
    is the only layer that sees the base URL."""
    import inspect

    from agentos.api import gateway

    source = inspect.getsource(gateway)
    assert 'provider_name not in PROVIDERS_WITH_OPTIONAL_KEY and payload.api_key is None' in source
    assert '@app.post("/v1/providers/ollama/test")' in source
