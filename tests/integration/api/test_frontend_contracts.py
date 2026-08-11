"""Fase 0 exit criterion: an authorized client creates, reads and follows a
real Execution through the production HTTP surface without ever touching
Postgres, Redis or opaque refs directly — see docs/frontend/
IMPLEMENTATION_PLAN.md, Fase 0.

Composes ``agentos.bootstrap.production.compose_production_services`` (the
real Postgres-backed security/execution/event adapters), never a fake or an
in-memory adapter, against a real database reachable via
``AGENTOS_TEST_POSTGRES_DSN`` — this project's own docker-compose Postgres
at postgresql://agentos@localhost:5433/agentos, or any DSN the runner sets.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.bootstrap.production import DependencyProbe, ProductionSettings, compose_production_services, create_production_app
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.security import PostgresSecurityService
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderModelCatalogService


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def _engine():
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return engine


def _settings() -> ProductionSettings:
    return ProductionSettings(DATABASE_URL=os.environ["AGENTOS_TEST_POSTGRES_DSN"], REDIS_URL="redis://localhost:6380/0")


def _client_with_pat(engine) -> tuple[TestClient, str, str]:
    from agentos.api.security import AuthenticatedPrincipal

    user_id = f"user:{uuid4().hex}"
    token = f"pat_{uuid4().hex}"
    security = PostgresSecurityService(engine)
    security.add_pat(token, AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}), "pat"))
    services = compose_production_services(engine)
    services.security = security
    app = create_production_app(_settings(), services=services, probe=DependencyProbe(lambda: True, lambda: True))
    return TestClient(app), token, user_id


def test_client_creates_reads_and_streams_a_real_execution_without_touching_infrastructure_directly() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/v1/executions",
        headers={**headers, "Idempotency-Key": f"create-{uuid4().hex}"},
        json={"agent_id": "agent-1", "task_ref": "task-ref-1"},
    )
    assert created.status_code == 202
    execution_id = created.json()["execution_id"]
    assert created.json()["state_version"] == 1

    fetched = client.get(f"/v1/executions/{execution_id}", headers=headers)
    assert fetched.status_code == 200
    view = fetched.json()
    assert view["execution_id"] == execution_id
    assert view["state"] == "QUEUED"
    assert view["result"] is None
    assert view["failure"] is None

    opened = client.post("/v1/events/streams", headers=headers, json={"execution_ids": [execution_id]})
    assert opened.status_code == 201
    stream_id = opened.json()["stream_id"]
    cursor = opened.json()["cursor"]

    read = client.post(f"/v1/events/streams/{stream_id}/read", headers=headers, json={"cursor": cursor})
    assert read.status_code == 200
    events = read.json()["events"]
    assert len(events) == 1
    assert events[0]["execution_id"] == execution_id
    assert events[0]["event_type"] == "ExecutionQueued"


def test_creating_twice_with_the_same_idempotency_key_does_not_duplicate_the_execution() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    idempotency_key = f"create-{uuid4().hex}"

    first = client.post("/v1/executions", headers={**headers, "Idempotency-Key": idempotency_key}, json={"agent_id": "agent-1", "task_ref": "task-ref-1"})
    second = client.post("/v1/executions", headers={**headers, "Idempotency-Key": idempotency_key}, json={"agent_id": "agent-1", "task_ref": "task-ref-1"})

    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["execution_id"] == second.json()["execution_id"]


def test_invalid_cursor_is_rejected_with_a_public_error_envelope() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/v1/executions", headers={**headers, "Idempotency-Key": f"create-{uuid4().hex}"}, json={"agent_id": "agent-1", "task_ref": "task-ref-1"})
    execution_id = created.json()["execution_id"]
    opened = client.post("/v1/events/streams", headers=headers, json={"execution_ids": [execution_id]})
    stream_id = opened.json()["stream_id"]

    response = client.post(f"/v1/events/streams/{stream_id}/read", headers=headers, json={"cursor": "c.tampered.signature"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cursor_invalid"
    assert "signature" not in response.text


def test_revoked_credential_can_no_longer_read_its_own_stream() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/v1/executions", headers={**headers, "Idempotency-Key": f"create-{uuid4().hex}"}, json={"agent_id": "agent-1", "task_ref": "task-ref-1"})
    execution_id = created.json()["execution_id"]
    opened = client.post("/v1/events/streams", headers=headers, json={"execution_ids": [execution_id]})
    stream_id, cursor = opened.json()["stream_id"], opened.json()["cursor"]

    from agentos.api.security import AuthenticatedPrincipal

    security = PostgresSecurityService(engine)
    principal = security.authenticate(bearer_token=token, session_id=None)
    security.revoke(principal.credential_ref)

    response = client.post(f"/v1/events/streams/{stream_id}/read", headers=headers, json={"cursor": cursor})

    assert response.status_code == 401


def test_get_execution_belonging_to_a_different_user_returns_not_found_not_someone_elses_data() -> None:
    engine = _engine()
    owner_client, owner_token, _owner_id = _client_with_pat(engine)
    stranger_client, stranger_token, _stranger_id = _client_with_pat(engine)
    created = owner_client.post(
        "/v1/executions",
        headers={"Authorization": f"Bearer {owner_token}", "Idempotency-Key": f"create-{uuid4().hex}"},
        json={"agent_id": "agent-1", "task_ref": "task-ref-1"},
    )
    execution_id = created.json()["execution_id"]

    response = stranger_client.get(f"/v1/executions/{execution_id}", headers={"Authorization": f"Bearer {stranger_token}"})

    assert response.status_code == 404
    assert execution_id not in response.text


def test_control_with_a_stale_expected_version_returns_409_conflict() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/v1/executions", headers={**headers, "Idempotency-Key": f"create-{uuid4().hex}"}, json={"agent_id": "agent-1", "task_ref": "task-ref-1"})
    execution_id = created.json()["execution_id"]

    response = client.post(
        f"/v1/executions/{execution_id}/control",
        headers={**headers, "Idempotency-Key": f"control-{uuid4().hex}"},
        json={"action": "CANCEL", "expected_state_version": 99},
    )

    assert response.status_code == 409
    assert response.json()["error"]["category"] == "CONFLICT"


def test_production_conversation_binds_cached_model_and_hides_the_opaque_prompt_reference() -> None:
    engine = _engine()
    client, token, user_id = _client_with_pat(engine)
    PostgresProviderConfigurationAdapter(engine).configure({"provider": "openrouter", "user_id": user_id, "enabled": True, "api_key": "test-key"})

    class Catalog:
        def fetch(self, api_key: str):
            assert api_key == "test-key"
            return [{"id": "anthropic/model-a", "name": "Model A", "context_length": 128000, "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": [], "pricing": {}}]

    ProviderModelCatalogService(PostgresProviderCatalogRepository(engine), {"openrouter": Catalog()}).refresh(
        ProviderCatalogContext(user_id, "provider.catalog.refresh"), "openrouter"
    )
    response = client.post(
        "/v1/conversations",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"conversation-{uuid4().hex}"},
        json={"message": "Organize os dados", "selection": {"provider": "openrouter", "model_id": "anthropic/model-a"}},
    )

    assert response.status_code == 201
    # The composed production surface is the durable chat application, so the
    # receipt names the conversation and its first turn — not an internal agent
    # or prompt reference.
    receipt = response.json()
    assert set(receipt) == {"conversation_id", "title", "turn_id", "message_id", "state"}
    assert receipt["state"] == "queued"
    with engine.connect() as connection:
        queued = connection.exec_driver_sql(
            "SELECT state, provider, model_id FROM conversation_turns WHERE turn_id = %s", (receipt["turn_id"],),
        ).one()
        dispatch = connection.exec_driver_sql(
            "SELECT state FROM conversation_dispatches WHERE turn_id = %s", (receipt["turn_id"],),
        ).scalar_one()
    assert queued == ("queued", "openrouter", "anthropic/model-a")
    # The turn must still be waiting for a worker rather than having been executed
    # inside the HTTP request. "enqueued" is equally valid here: if a real
    # publisher happens to be running against this database it will have moved
    # the row on already, and that is the same invariant.
    assert dispatch in {"pending", "enqueued"}
    assert "prompt:" not in response.text
    assert "test-key" not in response.text
