"""Fase D exit criterion: PUT/GET/DELETE /v1/providers/{provider} against a
real production composition, backed by real PostgreSQL — see docs/frontend/
PROJECT_CLOSEOUT_ROADMAP.md, Fase D.

The API key must never come back in any response body, matching the same
guarantee ``_provider_public()`` already enforces for a fake adapter in
``tests/unit/api/test_api_asgi.py``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import provider_configurations

from agentos.bootstrap.production import DependencyProbe, ProductionSettings, compose_production_services, create_production_app
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.security import PostgresSecurityService


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


def test_configure_then_inspect_round_trips_through_real_postgres_without_model_or_key_leakage() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    secret_api_key = f"sk-{uuid4().hex}"

    configured = client.put(
        "/v1/providers/openai",
        headers={**headers, "Idempotency-Key": f"provider-{uuid4().hex}"},
        json={"api_key": secret_api_key, "enabled": True},
    )
    assert configured.status_code == 200
    assert secret_api_key not in configured.text
    assert "api_key" not in configured.json()
    assert configured.json()["provider"] == "openai"
    assert "model" not in configured.json()
    assert configured.json()["enabled"] is True
    assert configured.json()["catalog_refreshed_at"] is None

    inspected = client.get("/v1/providers/openai", headers=headers)
    assert inspected.status_code == 200
    assert secret_api_key not in inspected.text
    assert "api_key" not in inspected.json()
    assert "model" not in inspected.json()
    assert inspected.json()["enabled"] is True
    assert inspected.json()["catalog_refreshed_at"] is None


def test_legacy_model_value_remains_credential_data_and_is_never_exposed_or_used_as_a_default() -> None:
    engine = _engine()
    client, token, user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    legacy_model = "legacy-provider-default"

    with engine.begin() as connection:
        connection.execute(
            provider_configurations.insert().values(
                user_id=user_id,
                provider="openrouter",
                enabled=True,
                model=legacy_model,
                api_key="legacy-key",
                secret_ref="provider-secret:legacy",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

    response = client.get("/v1/providers/openrouter", headers=headers)

    assert response.status_code == 200
    assert legacy_model not in response.text
    assert "model" not in response.json()
    assert response.json()["catalog_refreshed_at"] is None


def test_revoke_disables_the_provider_and_never_leaks_the_key() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}
    secret_api_key = f"sk-{uuid4().hex}"
    client.put(
        "/v1/providers/anthropic",
        headers={**headers, "Idempotency-Key": f"provider-{uuid4().hex}"},
        json={"api_key": secret_api_key, "enabled": True},
    )

    revoked = client.delete(
        "/v1/providers/anthropic",
        headers={**headers, "Idempotency-Key": f"provider-revoke-{uuid4().hex}"},
    )

    assert revoked.status_code == 200
    assert secret_api_key not in revoked.text
    assert revoked.json()["enabled"] is False

    inspected = client.get("/v1/providers/anthropic", headers=headers)
    assert inspected.json()["enabled"] is False


def test_inspecting_an_unconfigured_provider_returns_not_found() -> None:
    engine = _engine()
    client, token, _user_id = _client_with_pat(engine)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/v1/providers/openrouter", headers=headers)

    assert response.status_code == 404


def test_provider_configuration_is_isolated_per_user() -> None:
    engine = _engine()
    owner_client, owner_token, _owner_id = _client_with_pat(engine)
    stranger_client, stranger_token, _stranger_id = _client_with_pat(engine)
    secret_api_key = f"sk-{uuid4().hex}"
    owner_client.put(
        "/v1/providers/openai",
        headers={"Authorization": f"Bearer {owner_token}", "Idempotency-Key": f"provider-{uuid4().hex}"},
        json={"api_key": secret_api_key, "enabled": True},
    )

    response = stranger_client.get("/v1/providers/openai", headers={"Authorization": f"Bearer {stranger_token}"})

    assert response.status_code == 404
