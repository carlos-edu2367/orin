from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
import pytest

from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemorySecurityService,
    create_app,
)
from agentos.bootstrap.production import DependencyProbe, ProductionSettings, create_production_app


class FakeExecutionApplication:
    def create(self, command: dict[str, object]) -> dict[str, object]:
        return {"outcome": "accepted", "execution_id": command["context"]["execution_id"], "state_version": 1}

    def control(self, command: dict[str, object]) -> dict[str, object]:
        return {"outcome": "accepted", "execution_id": command["context"]["execution_id"], "state_version": 2}

    def provide_input(self, command: dict[str, object]) -> dict[str, object]:
        return {"outcome": "accepted", "execution_id": command["context"]["execution_id"], "state_version": 2}


class FakeProviderConfiguration:
    def configure(self, command: dict[str, object]) -> dict[str, object]:
        assert command["api_key"] == "key-writes-only"
        return {"provider": command["provider"], "enabled": True, "model": command["model"], "secret_ref": "secret-ref", "api_key": "must-not-leak"}

    def inspect(self, query: dict[str, object]) -> dict[str, object]:
        return {"provider": query["provider"], "enabled": True, "model": "gpt-test", "secret_ref": "secret-ref"}

    def revoke(self, command: dict[str, object]) -> dict[str, object]:
        return {"provider": command["provider"], "enabled": False, "secret_ref": "secret-ref"}


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

    saved = client.put("/v1/providers/openai", headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "provider-1"}, json={"api_key": "key-writes-only", "model": "gpt-test"})
    inspected = client.get("/v1/providers/openai", headers={"Authorization": "Bearer pat-test"})

    assert saved.status_code == 200
    assert "key-writes-only" not in saved.text and "must-not-leak" not in saved.text
    assert inspected.status_code == 200
    assert inspected.json()["model"] == "gpt-test"


def test_production_bootstrap_rejects_enabled_provider_without_model_and_exposes_sanitized_unready_status() -> None:
    with pytest.raises(ValueError):
        ProductionSettings(
            DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
            REDIS_URL="redis://localhost:6379/0",
            OPENAI_ENABLED=True,
            OPENAI_API_KEY="key",
        )

    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
        REDIS_URL="redis://localhost:6379/0",
        OPENAI_ENABLED=True,
        OPENAI_API_KEY="key",
        OPENAI_MODEL="gpt-test",
    )
    app = create_production_app(settings, probe=DependencyProbe(lambda: False, lambda: False))

    response = TestClient(app, raise_server_exceptions=False).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}

    with pytest.raises(ValueError, match="cannot use in-memory"):
        create_production_app(settings, services=ApiServices(), probe=DependencyProbe(lambda: True, lambda: True))
