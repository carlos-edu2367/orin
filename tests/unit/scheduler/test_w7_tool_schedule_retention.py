"""W7 Batch F (W4): the schedules a tool made are deleted a week after they finish; nothing else is."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select, update

from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog, schedule_occurrences, schedules
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatInput, ScheduledChatService

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _service():
    engine = create_local_engine("sqlite+pysqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=NOW, created_at=NOW, updated_at=NOW,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=NOW, created_at=NOW, updated_at=NOW,
        ))
    return engine, ScheduledChatService(engine, clock=lambda: NOW)


def _schedule(service: ScheduledChatService, engine, key: str, *, source: str, state: str, age: timedelta) -> str:
    request = ScheduledChatInput("lembrete", "openrouter", "model-1", "UTC", "once", fire_at=NOW + timedelta(hours=1), source=source)
    schedule_id = str(service.create("user-1", request, idempotency_key=key)["schedule_id"])
    with engine.begin() as connection:
        connection.execute(update(schedules).where(schedules.c.schedule_id == schedule_id).values(state=state, updated_at=NOW - age))
    return schedule_id


def _ids(engine) -> set[str]:
    with engine.connect() as connection:
        return {str(item) for item in connection.execute(select(schedules.c.schedule_id)).scalars()}


def test_only_old_finished_tool_schedules_are_deleted() -> None:
    engine, service = _service()
    old_wakeup = _schedule(service, engine, "a", source="wakeup", state="EXPIRED", age=timedelta(days=8))
    old_monitor = _schedule(service, engine, "b", source="monitor", state="CANCELLED", age=timedelta(days=30))
    recent = _schedule(service, engine, "c", source="wakeup", state="EXPIRED", age=timedelta(days=6))
    mine = _schedule(service, engine, "d", source="user", state="EXPIRED", age=timedelta(days=90))
    active = _schedule(service, engine, "e", source="wakeup", state="ACTIVE", age=timedelta(days=90))
    assert service.prune_finished(now=NOW) == 2
    assert _ids(engine) == {recent, mine, active}
    assert old_wakeup not in _ids(engine) and old_monitor not in _ids(engine)


def test_a_schedule_with_a_run_in_flight_is_kept() -> None:
    engine, service = _service()
    busy = _schedule(service, engine, "a", source="wakeup", state="EXPIRED", age=timedelta(days=8))
    with engine.begin() as connection:
        connection.execute(insert(schedule_occurrences).values(
            occurrence_id=f"{busy}:1:x", schedule_id=busy, schedule_version=1, logical_scheduled_at=NOW, state="MATERIALIZED",
            version=1, state_fencing_token=1, created_at=NOW, updated_at=NOW,
        ))
    assert service.prune_finished(now=NOW) == 0 and busy in _ids(engine)


def test_the_poller_prunes_at_most_once_an_hour(monkeypatch) -> None:
    engine, service = _service()
    calls: list[datetime] = []
    monkeypatch.setattr(service, "prune_finished", lambda *, now=None: calls.append(now) or 0)
    service.run_due(worker_id="w1", due_before=NOW)
    service.run_due(worker_id="w1", due_before=NOW)
    assert len(calls) == 1
