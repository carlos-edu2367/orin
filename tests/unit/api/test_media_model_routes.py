from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.persistence.postgres.schema import metadata
from agentos.reading.store import PostgresMediaModelSettingsStore

_AUTH = {"Authorization": "Bearer pat-test", "Idempotency-Key": "i1"}


def _client(tmp_path: Path) -> TestClient:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    services = ApiServices(security=security, media_model_settings=PostgresMediaModelSettingsStore(engine))
    return TestClient(create_app(services))


def test_get_media_models_returns_automatic_for_all_modalities(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/v1/settings/media-models", headers=_AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "image": {"provider": None, "model_id": None, "mode": "automatic"},
        "audio": {"provider": None, "model_id": None, "mode": "automatic"},
        "video": {"provider": None, "model_id": None, "mode": "automatic"},
    }


def test_put_media_model_stores_per_modality_and_clears_back_to_automatic(tmp_path: Path) -> None:
    client = _client(tmp_path)

    resp_img = client.put(
        "/v1/settings/media-models/image",
        json={"provider": "openrouter", "model_id": "qwen/qwen-image-2.1-pro"},
        headers=_AUTH,
    )
    assert resp_img.status_code == 200
    assert resp_img.json() == {
        "modality": "image",
        "provider": "openrouter",
        "model_id": "qwen/qwen-image-2.1-pro",
        "mode": "manual",
    }

    resp_aud = client.put(
        "/v1/settings/media-models/audio",
        json={"provider": "openrouter", "model_id": "elevenlabs/eleven-v3"},
        headers=_AUTH,
    )
    assert resp_aud.status_code == 200
    assert resp_aud.json() == {
        "modality": "audio",
        "provider": "openrouter",
        "model_id": "elevenlabs/eleven-v3",
        "mode": "manual",
    }

    all_settings = client.get("/v1/settings/media-models", headers=_AUTH).json()
    assert all_settings["image"] == {
        "provider": "openrouter",
        "model_id": "qwen/qwen-image-2.1-pro",
        "mode": "manual",
    }
    assert all_settings["audio"] == {
        "provider": "openrouter",
        "model_id": "elevenlabs/eleven-v3",
        "mode": "manual",
    }
    assert all_settings["video"] == {"provider": None, "model_id": None, "mode": "automatic"}

    # Clearing image returns it to automatic without touching audio
    clear_img = client.put(
        "/v1/settings/media-models/image",
        json={"provider": None, "model_id": None},
        headers=_AUTH,
    )
    assert clear_img.status_code == 200
    assert clear_img.json()["mode"] == "automatic"
    after_clear = client.get("/v1/settings/media-models", headers=_AUTH).json()
    assert after_clear["image"]["mode"] == "automatic"
    assert after_clear["audio"]["model_id"] == "elevenlabs/eleven-v3"


def test_put_media_model_rejects_malformed_requests(tmp_path: Path) -> None:
    client = _client(tmp_path)

    # Only one field present
    r1 = client.put("/v1/settings/media-models/image", json={"provider": "openrouter", "model_id": None}, headers=_AUTH)
    assert r1.status_code == 422

    # Blank model_id
    r2 = client.put("/v1/settings/media-models/image", json={"provider": "openrouter", "model_id": "   "}, headers=_AUTH)
    assert r2.status_code == 422

    # Unknown provider
    r3 = client.put("/v1/settings/media-models/image", json={"provider": "fakeprovider", "model_id": "m1"}, headers=_AUTH)
    assert r3.status_code == 422

    # Unknown modality
    r4 = client.put("/v1/settings/media-models/3d", json={"provider": "openrouter", "model_id": "m1"}, headers=_AUTH)
    assert r4.status_code == 422
