"""W4 hardening: a message a tool scheduled is never the person's authorization to git push."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    conversation_turns, metadata, provider_configurations, provider_model_catalog,
)
from agentos.scheduler.scheduled_chats import ScheduledChatInput, ScheduledChatService
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker

PUSH = "Rode os testes e faça git push para a origin"


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())
    now = datetime.now(UTC)
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="anthropic", model_id="claude-sonnet-4-6", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="anthropic", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    store = PostgresChatStore(engine)
    first = store.create(user_id="user-1", message="Olá", provider="anthropic", model_id="claude-sonnet-4-6", idempotency_key="first", code_mode="code")
    store.finish(store.claim(first.turn_id))
    return engine, store, first.conversation_id, ChatWorker(store, workspace_root=str(tmp_path))


def _scheduled_turn(engine, store, conversation_id: str, source: str):
    clock = {"now": datetime.now(UTC)}
    service = ScheduledChatService(engine, clock=lambda: clock["now"])
    receipt = service.create("user-1", ScheduledChatInput(
        PUSH, "anthropic", "claude-sonnet-4-6", "UTC", "once", fire_at=clock["now"] + timedelta(minutes=1),
        conversation_id=conversation_id, source=source,
    ), idempotency_key=f"push-{source}")
    clock["now"] += timedelta(minutes=2)
    assert service.run_due(worker_id="w", due_before=clock["now"])
    with engine.connect() as connection:
        turn_id = connection.execute(select(conversation_turns.c.turn_id).where(
            conversation_turns.c.scheduled_by_schedule_id == receipt["schedule_id"],
        )).scalar_one()
    return store.claim(turn_id)


@pytest.mark.parametrize("source", ["cron", "wakeup", "monitor"])
def test_a_tool_made_schedule_never_authorizes_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    engine, store, conversation_id, worker = _setup(tmp_path, monkeypatch)
    hydrated = worker._hydrate_code_mode(_scheduled_turn(engine, store, conversation_id, source))
    assert hydrated["code_mode_push_authorized"] is False


def test_a_schedule_the_person_wrote_still_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, store, conversation_id, worker = _setup(tmp_path, monkeypatch)
    hydrated = worker._hydrate_code_mode(_scheduled_turn(engine, store, conversation_id, "user"))
    assert hydrated["code_mode_push_authorized"] is True


def test_the_persons_own_message_still_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, store, conversation_id, worker = _setup(tmp_path, monkeypatch)
    receipt = store.create(user_id="user-1", message=PUSH, provider="anthropic", model_id="claude-sonnet-4-6", idempotency_key="own", conversation_id=conversation_id)
    hydrated = worker._hydrate_code_mode(store.claim(receipt.turn_id))
    assert hydrated["code_mode_push_authorized"] is True
