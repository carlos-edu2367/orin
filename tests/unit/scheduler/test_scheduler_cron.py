"""W4: the scheduler runs cron schedules bound to one conversation, and stops them when they expire."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    conversation_turns, metadata, provider_configurations, provider_model_catalog, schedules,
)
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatInput, ScheduledChatService


def _engine(now: datetime):
    engine = create_local_engine("sqlite+pysqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    return engine


def _conversation(engine) -> str:
    receipt = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="model-1", idempotency_key="c1")
    return receipt.conversation_id


def test_a_cron_schedule_fires_in_its_conversation_and_expires() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id = _conversation(engine)
    clock = {"now": now}
    service = ScheduledChatService(engine, clock=lambda: clock["now"])
    receipt = service.create("user-1", ScheduledChatInput(
        "check the build", "openrouter", "model-1", "UTC", "cron", cron="*/30 * * * *",
        conversation_id=conversation_id, ends_at=now + timedelta(hours=1), source="cron",
    ), idempotency_key="cron-1")
    assert receipt["next_fire_at"] == "2026-10-19T12:30:00Z"
    assert service.run_due(worker_id="w", due_before=now + timedelta(minutes=30))
    assert service.run_due(worker_id="w", due_before=now + timedelta(minutes=61))
    with engine.connect() as connection:
        turns = connection.execute(select(conversation_turns).where(conversation_turns.c.conversation_id == conversation_id)).mappings().all()
        state = connection.execute(select(schedules.c.state).where(schedules.c.schedule_id == receipt["schedule_id"])).scalar_one()
    assert len(turns) == 3  # the first message plus two scheduled runs
    assert state == "EXPIRED"  # 13:30 would be after ends_at


def test_another_users_conversation_is_refused() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id = _conversation(engine)
    service = ScheduledChatService(engine, clock=lambda: now)
    with pytest.raises(LookupError):
        service.create("user-2", ScheduledChatInput(
            "x", "openrouter", "model-1", "UTC", "cron", cron="0 9 * * *", conversation_id=conversation_id,
        ), idempotency_key="cron-2")


def test_conversation_schedules_are_listed_by_source() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id = _conversation(engine)
    service = ScheduledChatService(engine, clock=lambda: now)
    service.create("user-1", ScheduledChatInput("a", "openrouter", "model-1", "UTC", "cron", cron="0 9 * * *", conversation_id=conversation_id, source="cron"), idempotency_key="k1")
    service.create("user-1", ScheduledChatInput("b", "openrouter", "model-1", "UTC", "once", fire_at=now + timedelta(minutes=5), conversation_id=conversation_id, source="wakeup"), idempotency_key="k2")
    service.create("user-1", ScheduledChatInput("c", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="k3")
    rows = service.list_for_conversation("user-1", conversation_id, sources=("cron",))
    assert [row["message"] for row in rows] == ["a"] and rows[0]["cron"] == "0 9 * * *"
    assert [row["message"] for row in service.list_for_conversation("user-1", conversation_id, sources=("wakeup",))] == ["b"]
    assert service.list_for_conversation("user-2", conversation_id, sources=("cron",)) == []


def test_a_bad_cron_expression_is_refused() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    service = ScheduledChatService(_engine(now), clock=lambda: now)
    with pytest.raises(ValueError):
        service.create("user-1", ScheduledChatInput("x", "openrouter", "model-1", "UTC", "cron", cron="61 * * * *"), idempotency_key="bad")
