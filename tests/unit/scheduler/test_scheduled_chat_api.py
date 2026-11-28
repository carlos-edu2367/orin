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
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now,
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


def test_scheduled_chat_http_cron_edit_pause_resume_and_permanent_delete(tmp_path: Path) -> None:
    from agentos.persistence.postgres.schema import conversations

    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    engine = create_local_engine(sqlite_url(tmp_path / "scheduled-chat-crud.db"))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values([
            dict(
                user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model One",
                capabilities=["tools"], input_modalities=["text"], output_modalities=["text"],
                refreshed_at=now, created_at=now, updated_at=now,
            ),
            dict(
                user_id="user-1", provider="google-antigravity", model_id="gemini-4-neon-high",
                display_name="Gemini 4 Neon (High)", capabilities=["tools"], input_modalities=["text"],
                output_modalities=["text"], refreshed_at=now, created_at=now, updated_at=now,
            ),
            dict(
                user_id="user-1", provider="google-antigravity", model_id="gemini-4-carbon-high",
                display_name="Gemini 4 Carbon (High)", capabilities=["tools"], input_modalities=["text"],
                output_modalities=["text"], refreshed_at=now, created_at=now, updated_at=now,
            ),
        ]))
        connection.execute(insert(provider_configurations).values([
            dict(
                user_id="user-1", provider="openrouter", enabled=True, model=None,
                base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now,
                created_at=now, updated_at=now,
            ),
            dict(
                user_id="user-1", provider="google-antigravity", enabled=True, model=None,
                base_url=None, secret_ref="test-ag", key_cooldown_seconds=60, catalog_refreshed_at=now,
                created_at=now, updated_at=now,
            ),
        ]))
        connection.execute(insert(conversations).values(
            conversation_id="chat-burocracias", user_id="user-1", title="Freelas e Vagas",
            provider="google-antigravity", model_id="gemini-4-carbon-high", state="completed",
            created_at=now, updated_at=now, project_id=None, mode="code", permission_mode="default",
        ))

    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, scheduled_chats=ScheduledChatService(engine, clock=lambda: now)))
    client = TestClient(app)
    auth = {"Authorization": "Bearer pat-test"}

    # Create cron schedule directly via HTTP API
    created = client.post(
        "/v1/schedules",
        headers={**auth, "Idempotency-Key": "schedule-cron-create"},
        json={
            "message": "Sessão 2 - Burocracias",
            "selection": {"provider": "openrouter", "model_id": "model-1"},
            "timezone": "America/Sao_Paulo",
            "recurrence": {"kind": "cron", "expression": "0 15 * * *"},
            "project_id": None,
        },
    )
    assert created.status_code == 201
    schedule_id = created.json()["schedule_id"]

    # Edit message, switch model/provider to Neon, change cron time & timezone
    updated = client.patch(
        f"/v1/schedules/{schedule_id}",
        headers={**auth, "Idempotency-Key": "schedule-cron-edit"},
        json={
            "message": "Sessão 2 atualizada",
            "selection": {"provider": "google-antigravity", "model_id": "gemini-4-neon-high"},
            "timezone": "UTC",
            "recurrence": {"kind": "cron", "expression": "30 16 * * 1-5"},
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["message"] == "Sessão 2 atualizada"
    assert body["provider"] == "google-antigravity"
    assert body["model_id"] == "gemini-4-neon-high"
    assert body["model_display_name"] == "Gemini 4 Neon (High)"
    assert body["cron"] == "30 16 * * 1-5"
    assert body["timezone"] == "UTC"
    assert body["state"] == "ACTIVE"

    # Pause and resume
    paused = client.patch(
        f"/v1/schedules/{schedule_id}",
        headers={**auth, "Idempotency-Key": "schedule-cron-pause"},
        json={"state": "PAUSED"},
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "PAUSED"
    assert paused.json()["next_fire_at"] is None

    resumed = client.patch(
        f"/v1/schedules/{schedule_id}",
        headers={**auth, "Idempotency-Key": "schedule-cron-resume"},
        json={"state": "ACTIVE"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "ACTIVE"
    assert resumed.json()["next_fire_at"] is not None

    # Cancel then permanently delete a cancelled schedule
    assert client.delete(f"/v1/schedules/{schedule_id}", headers={**auth, "Idempotency-Key": "schedule-cancel"}).status_code == 204
    assert client.get("/v1/schedules", headers=auth).json()["items"][0]["state"] == "CANCELLED"

    deleted = client.delete(
        f"/v1/schedules/{schedule_id}?permanent=true",
        headers={**auth, "Idempotency-Key": "schedule-perm-delete"},
    )
    assert deleted.status_code == 204
    assert client.get("/v1/schedules", headers=auth).json()["items"] == []
