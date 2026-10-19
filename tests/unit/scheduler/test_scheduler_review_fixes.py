"""W4 review fixes for scheduled runs: a run never answers a pending question (R7) and downtime fires once (R8)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, insert, select, update

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    conversation_turns, metadata, provider_configurations, provider_model_catalog, schedule_occurrences, schedules,
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


def _conversation_with_turn_in_state(engine, state: str) -> tuple[str, str]:
    receipt = PostgresChatStore(engine).create(user_id="user-1", message="migrate the db", provider="openrouter", model_id="model-1", idempotency_key="c1")
    with engine.begin() as connection:
        connection.execute(update(conversation_turns).where(conversation_turns.c.turn_id == receipt.turn_id).values(state=state))
    return receipt.conversation_id, receipt.turn_id


def _turn_count(engine, conversation_id: str) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(conversation_turns).where(
            conversation_turns.c.conversation_id == conversation_id,
        )).scalar_one()


def _schedule_row(engine, schedule_id: str):
    with engine.connect() as connection:
        return connection.execute(select(schedules).where(schedules.c.schedule_id == schedule_id)).mappings().one()


def _occurrences(engine, schedule_id: str) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(schedule_occurrences).where(
            schedule_occurrences.c.schedule_id == schedule_id,
        )).scalar_one()


# R7: a scheduled run never answers the conversation's pending question or approval.

def test_a_due_wakeup_waits_while_the_conversation_asks_a_question() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id, question_turn = _conversation_with_turn_in_state(engine, "waiting_user")
    clock = {"now": now}
    service = ScheduledChatService(engine, clock=lambda: clock["now"])
    receipt = service.create("user-1", ScheduledChatInput(
        "The user approved dropping the legacy table; continue.", "openrouter", "model-1", "UTC", "once",
        fire_at=now + timedelta(minutes=1), conversation_id=conversation_id, source="wakeup",
    ), idempotency_key="wake-1")
    due_at = now + timedelta(minutes=2)
    before = _schedule_row(engine, receipt["schedule_id"])

    assert service.run_due(worker_id="w", due_before=due_at) == ()

    after = _schedule_row(engine, receipt["schedule_id"])
    assert after["state"] == "ACTIVE"  # not EXPIRED: the run is deferred, not dropped
    assert after["next_fire_at"] == before["next_fire_at"]  # the due time is kept for a later poll
    assert _occurrences(engine, receipt["schedule_id"]) == 0  # no occurrence row for a deferred run
    assert _turn_count(engine, conversation_id) == 1
    with engine.connect() as connection:
        state = connection.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == question_turn)).scalar_one()
    assert state == "waiting_user"  # the question is still open

    # The person answers; the next poll runs the deferred wakeup exactly once.
    with engine.begin() as connection:
        connection.execute(update(conversation_turns).where(conversation_turns.c.turn_id == question_turn).values(state="completed"))
    assert len(service.run_due(worker_id="w", due_before=due_at)) == 1
    assert _turn_count(engine, conversation_id) == 2
    assert _schedule_row(engine, receipt["schedule_id"])["state"] == "EXPIRED"


def test_a_recurring_run_waits_while_its_conversation_is_busy() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id, _ = _conversation_with_turn_in_state(engine, "running")
    service = ScheduledChatService(engine, clock=lambda: now)
    receipt = service.create("user-1", ScheduledChatInput(
        "check the deploy", "openrouter", "model-1", "UTC", "cron", cron="*/5 * * * *",
        conversation_id=conversation_id, source="cron",
    ), idempotency_key="cron-busy")
    due_at = now + timedelta(minutes=6)
    before = _schedule_row(engine, receipt["schedule_id"])

    assert service.run_due(worker_id="w", due_before=due_at) == ()
    after = _schedule_row(engine, receipt["schedule_id"])
    assert after["next_fire_at"] == before["next_fire_at"] and after["state"] == "ACTIVE"
    assert _occurrences(engine, receipt["schedule_id"]) == 0

    with engine.begin() as connection:
        connection.execute(update(conversation_turns).where(conversation_turns.c.conversation_id == conversation_id).values(state="completed"))
    assert len(service.run_due(worker_id="w", due_before=due_at)) == 1


def test_a_scheduled_create_leaves_a_waiting_turn_waiting() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id, question_turn = _conversation_with_turn_in_state(engine, "waiting_user")
    store = PostgresChatStore(engine)

    store.create(
        user_id="user-1", message="continue", provider="openrouter", model_id="model-1", idempotency_key="scheduled-1",
        conversation_id=conversation_id, scheduled_by_schedule_id="schedule_test",
    )

    with engine.connect() as connection:
        state = connection.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == question_turn)).scalar_one()
    assert state == "waiting_user"


def test_a_user_create_still_closes_the_waiting_turn() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id, question_turn = _conversation_with_turn_in_state(engine, "waiting_user")

    PostgresChatStore(engine).create(
        user_id="user-1", message="yes", provider="openrouter", model_id="model-1", idempotency_key="user-1",
        conversation_id=conversation_id,
    )

    with engine.connect() as connection:
        state = connection.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == question_turn)).scalar_one()
    assert state == "completed"


# R8: a recurring schedule that missed fires while Orin was down fires once, then continues from now.

def test_a_three_hour_gap_fires_a_cron_schedule_once() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    conversation_id = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="model-1", idempotency_key="c1").conversation_id
    clock = {"now": now}
    service = ScheduledChatService(engine, clock=lambda: clock["now"])
    receipt = service.create("user-1", ScheduledChatInput(
        "check the deploy", "openrouter", "model-1", "UTC", "cron", cron="*/5 * * * *",
        conversation_id=conversation_id, ends_at=now + timedelta(days=7), source="cron",
    ), idempotency_key="cron-gap")

    clock["now"] = now + timedelta(hours=3)
    fired = sum(len(service.run_due(worker_id="w")) for _ in range(50))

    assert fired == 1
    assert _occurrences(engine, receipt["schedule_id"]) == 1
    assert _turn_count(engine, conversation_id) == 2
    next_fire = _schedule_row(engine, receipt["schedule_id"])["next_fire_at"]
    assert next_fire.replace(tzinfo=UTC) > clock["now"]


def test_a_three_hour_gap_fires_an_hourly_schedule_once_and_stays_on_its_grid() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    engine = _engine(now)
    clock = {"now": now}
    service = ScheduledChatService(engine, clock=lambda: clock["now"])
    receipt = service.create("user-1", ScheduledChatInput("hourly check", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="hourly-gap")

    clock["now"] = now + timedelta(hours=3, seconds=20)
    fired = sum(len(service.run_due(worker_id="w")) for _ in range(50))

    assert fired == 1
    next_fire = _schedule_row(engine, receipt["schedule_id"])["next_fire_at"].replace(tzinfo=UTC)
    assert next_fire > clock["now"]
    assert next_fire == datetime(2026, 10, 19, 16, 0, tzinfo=UTC)  # the hourly grid anchored at 13:00, not at the poll time
