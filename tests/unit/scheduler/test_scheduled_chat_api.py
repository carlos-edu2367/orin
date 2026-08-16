from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import insert

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.scheduler.scheduled_chats import ScheduledChatService


def test_scheduled_chat_http_flow_creates_lists_and_cancels_with_sqlite_foreign_keys(tmp_path: Path) -> None:
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    engine = create_local_engine(sqlite_url(tmp_path / "scheduled-chat-api.db"))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=["tools"], input_modalities=["text"], output_modalities=["text"],
            refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None, api_key=None,
            api_key_ciphertext=None, base_url=None, secret_ref="test", catalog_refreshed_at=now,
            created_at=now, updated_at=now,
        ))

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, scheduled_chats=ScheduledChatService(engine, clock=lambda: now)))
    client = TestClient(app)
    headers = {"Authorization": "Bearer pat-test", "Idempotency-Key": "schedule-http-1"}

    created = client.post(
        "/v1/schedules",
        headers=headers,
        json={
            "message": "Verifique o relatório",
            "selection": {"provider": "openrouter", "model_id": "model-1"},
            "timezone": "UTC",
            "recurrence": {"kind": "hourly"},
            "project_id": None,
        },
    )
    assert created.status_code == 201
    schedule_id = created.json()["schedule_id"]

    listed = client.get("/v1/schedules", headers={"Authorization": "Bearer pat-test"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["schedule_id"] == schedule_id

    cancelled = client.delete(f"/v1/schedules/{schedule_id}", headers={**headers, "Idempotency-Key": "schedule-http-cancel-1"})
    assert cancelled.status_code == 204
    assert client.get("/v1/schedules", headers={"Authorization": "Bearer pat-test"}).json()["items"][0]["state"] == "CANCELLED"
