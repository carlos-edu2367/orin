from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.agentic_activity import ActivityCursorError, PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import conversation_activity_events


pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTOS_TEST_POSTGRES_DSN"),
    reason="AGENTOS_TEST_POSTGRES_DSN is not configured",
)


def _store():
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return engine, PostgresAgenticActivityStore(engine, cursor_secret="test-secret")


def _event(event_id: str, user_id: str, sequence: int, *, conversation_id: str = "conversation:1"):
    return AgentActivityEvent(
        event_id=event_id,
        conversation_id=conversation_id,
        turn_id="turn:1",
        execution_id="execution:1",
        user_id=user_id,
        agent_id="agent:1",
        event_type=AgentActivityEventType.TURN_STARTED,
        sequence=sequence,
        summary=f"activity {sequence}",
        payload={"execution_ref": "execution:1"},
        created_at=datetime.now(UTC),
    )


def test_activity_insert_is_durable_idempotent_and_conflict_checked():
    engine, store = _store()
    suffix = uuid4().hex
    event = _event(f"activity:{suffix}:1", f"user:{suffix}", 1, conversation_id=f"conversation:{suffix}")

    assert store.record_event(event) is True
    assert store.record_event(event) is False
    with pytest.raises(ValueError, match="different event"):
        store.record_event(_event(event.event_id, event.user_id, 2, conversation_id=event.conversation_id))

    with engine.connect() as connection:
        assert connection.execute(
            select(conversation_activity_events.c.event_id).where(
                conversation_activity_events.c.event_id == event.event_id
            )
        ).all() == [(event.event_id,)]


def test_activity_replay_is_owner_scoped_ordered_and_cursor_paginated():
    engine, store = _store()
    suffix = uuid4().hex
    user1 = f"user:{suffix}:1"
    user2 = f"user:{suffix}:2"
    conversation_id = f"conversation:{suffix}"
    event1 = _event(f"activity:{suffix}:user1:1", user1, 1, conversation_id=conversation_id)
    event2 = _event(f"activity:{suffix}:user1:2", user1, 2, conversation_id=conversation_id)
    event3 = _event(f"activity:{suffix}:user2:1", user2, 1, conversation_id=conversation_id)
    store.record_event(event1)
    store.record_event(event2)
    store.record_event(event3)

    first_page = store.replay(user1, conversation_id, limit=1)
    assert [item.event_id for item in first_page.events] == [event1.event_id]
    assert first_page.next_cursor is not None

    second_page = store.replay(
        user1, conversation_id, cursor=first_page.next_cursor, limit=10
    )
    assert [item.event_id for item in second_page.events] == [event2.event_id]
    assert [item.event_id for item in store.replay(user2, conversation_id).events] == [event3.event_id]


def test_activity_replay_rejects_tampered_cursor_with_resync_signal():
    _, store = _store()
    suffix = uuid4().hex
    user_id = f"user:{suffix}"
    conversation_id = f"conversation:{suffix}"
    store.record_event(_event(f"activity:{suffix}:cursor:1", user_id, 1, conversation_id=conversation_id))
    store.record_event(_event(f"activity:{suffix}:cursor:2", user_id, 2, conversation_id=conversation_id))
    page = store.replay(user_id, conversation_id, limit=1)

    with pytest.raises(ActivityCursorError) as error:
        store.replay(
            user_id,
            conversation_id,
            cursor=f"{page.next_cursor}.tampered",
        )
    assert error.value.code == "cursor_invalid"
    assert error.value.resync_required is True
