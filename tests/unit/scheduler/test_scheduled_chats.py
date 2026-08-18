from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, select

from agentos.persistence.postgres.schema import conversation_turns, metadata, provider_configurations, provider_model_catalog, scheduled_chat_tasks
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatInput, ScheduledChatService


def _ready(engine, now):
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))


def test_hourly_scheduled_chat_materializes_normal_marked_turns_in_one_shared_conversation():
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    engine = create_local_engine("sqlite+pysqlite://")
    metadata.create_all(engine)
    _ready(engine, now)
    service = ScheduledChatService(engine, clock=lambda: now)
    receipt = service.create("user-1", ScheduledChatInput("Verifique o relatório", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="schedule-1")

    assert service.run_due(worker_id="worker", due_before=now + timedelta(hours=1))
    assert service.run_due(worker_id="worker", due_before=now + timedelta(hours=2))
    with engine.connect() as connection:
        task = connection.execute(select(scheduled_chat_tasks).where(scheduled_chat_tasks.c.schedule_id == receipt["schedule_id"])).mappings().one()
        turns = connection.execute(select(conversation_turns).where(conversation_turns.c.conversation_id == task["conversation_id"])).mappings().all()
    assert len(turns) == 2
    assert all(turn["scheduled_by_schedule_id"] == receipt["schedule_id"] for turn in turns)


def test_daily_rule_keeps_local_civil_time():
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    service = ScheduledChatService(create_engine("sqlite://", future=True), clock=lambda: now)
    request = ScheduledChatInput("x", "p", "m", "America/Sao_Paulo", "daily", time_of_day="09:30")
    from zoneinfo import ZoneInfo

    assert service.next_fire(request).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M") == "09:30"


def test_list_serializes_once_schedule_as_explicit_utc_for_local_display():
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    _ready(engine, now)
    service = ScheduledChatService(engine, clock=lambda: now)
    service.create(
        "user-1",
        ScheduledChatInput(
            "x", "openrouter", "model-1", "America/Sao_Paulo", "once",
            fire_at=datetime(2026, 8, 15, 23, 50),
        ),
        idempotency_key="schedule-local-once",
    )

    listed = service.list("user-1")

    assert listed["items"][0]["next_fire_at"] == "2026-08-16T02:50:00Z"
