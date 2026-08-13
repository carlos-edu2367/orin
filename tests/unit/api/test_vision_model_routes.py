from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.persistence.postgres.schema import metadata
from agentos.reading.store import PostgresVisionModelSettingsStore

_AUTH = {"Authorization": "Bearer pat-test", "Idempotency-Key": "i1"}


def _client(tmp_path: Path) -> TestClient:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    services = ApiServices(security=security, vision_model_settings=PostgresVisionModelSettingsStore(engine))
    return TestClient(create_app(services))


def test_get_returns_automatic_when_nothing_is_selected(tmp_path):
    response = _client(tmp_path).get("/v1/settings/vision-model", headers=_AUTH)
    assert response.status_code == 200
    assert response.json() == {"provider": None, "model_id": None, "mode": "automatic"}


def test_put_stores_the_override(tmp_path):
    client = _client(tmp_path)
    response = client.put("/v1/settings/vision-model", json={"provider": "ollama", "model_id": "qwen2.5-vl"}, headers=_AUTH)
    assert response.status_code == 200
    assert response.json() == {"provider": "ollama", "model_id": "qwen2.5-vl", "mode": "manual"}
    assert client.get("/v1/settings/vision-model", headers=_AUTH).json()["model_id"] == "qwen2.5-vl"


def test_put_null_returns_to_automatic(tmp_path):
    client = _client(tmp_path)
    client.put("/v1/settings/vision-model", json={"provider": "ollama", "model_id": "qwen2.5-vl"}, headers=_AUTH)
    response = client.put("/v1/settings/vision-model", json={"provider": None, "model_id": None}, headers=_AUTH)
    assert response.json()["mode"] == "automatic"
    assert client.get("/v1/settings/vision-model", headers=_AUTH).json() == {"provider": None, "model_id": None, "mode": "automatic"}


def test_put_rejects_only_one_field_present(tmp_path):
    client = _client(tmp_path)
    response = client.put("/v1/settings/vision-model", json={"provider": "ollama", "model_id": None}, headers=_AUTH)
    assert response.status_code == 422
