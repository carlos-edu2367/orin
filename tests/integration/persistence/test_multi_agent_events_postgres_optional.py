from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.events import DataClassification, EventEnvelope
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.multi_agent_events import PostgresMultiAgentEventRecorder


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def _engine():
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return engine


def _delegation_created(execution_id: str, user_id: str, event_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type="DelegationCreated",
        event_version=1,
        occurred_at=datetime.now(UTC),
        source="multi-agent",
        correlation_id=f"corr_{execution_id}",
        causation_id="idem-1",
        sequence=1,
        user_id=user_id,
        workspace_id=None,
        execution_id=execution_id,
        classification=DataClassification.INTERNAL,
        payload={"delegation_id": f"delegation:{uuid4().hex}", "parent_execution_id": f"exe_{uuid4().hex}"},
        agent_id="agent-child",
    )


def _agent_message_created(execution_id: str, user_id: str, event_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type="AgentMessageCreated",
        event_version=1,
        occurred_at=datetime.now(UTC),
        source="multi-agent",
        correlation_id=f"corr_{execution_id}",
        causation_id="idem-2",
        sequence=1,
        user_id=user_id,
        workspace_id=None,
        execution_id=execution_id,
        classification=DataClassification.INTERNAL,
        payload={"message_id": f"message:{uuid4().hex}", "reason_code": "CREATED", "delivery_execution_id": execution_id},
        agent_id="agent-recipient",
    )


def test_recording_a_delegation_and_a_message_event_persists_both() -> None:
    engine = _engine()
    recorder = PostgresMultiAgentEventRecorder(engine)
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    delegation_event = _delegation_created(execution_id, user_id, f"event:{uuid4().hex}")
    message_event = _agent_message_created(execution_id, user_id, f"event:{uuid4().hex}")

    assert recorder.record_event(delegation_event) is True
    assert recorder.record_event(message_event) is True


def test_recording_the_same_event_id_twice_with_identical_content_is_idempotent() -> None:
    engine = _engine()
    recorder = PostgresMultiAgentEventRecorder(engine)
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    event_id = f"event:{uuid4().hex}"
    event = _delegation_created(execution_id, user_id, event_id)

    first = recorder.record_event(event)
    second = recorder.record_event(event)

    assert first is True
    assert second is False


def test_recording_the_same_event_id_with_different_content_raises() -> None:
    engine = _engine()
    recorder = PostgresMultiAgentEventRecorder(engine)
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    event_id = f"event:{uuid4().hex}"
    original = _delegation_created(execution_id, user_id, event_id)
    conflicting = EventEnvelope(
        event_id=event_id,
        event_type="DelegationCreated",
        event_version=1,
        occurred_at=original.occurred_at,
        source="multi-agent",
        correlation_id=original.correlation_id,
        causation_id="different-causation",
        sequence=1,
        user_id=user_id,
        workspace_id=None,
        execution_id=execution_id,
        classification=DataClassification.INTERNAL,
        payload={"delegation_id": "a-different-delegation", "parent_execution_id": "exe-other"},
        agent_id="agent-child",
    )
    recorder.record_event(original)

    with pytest.raises(ValueError):
        recorder.record_event(conflicting)


def test_recorder_persists_across_a_second_instance() -> None:
    # production is stateless across requests/processes; recorded events must
    # be durable, not held only in the adapter instance's memory.
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    event_id = f"event:{uuid4().hex}"
    event = _delegation_created(execution_id, user_id, event_id)
    PostgresMultiAgentEventRecorder(engine).record_event(event)

    second_result = PostgresMultiAgentEventRecorder(engine).record_event(event)

    assert second_result is False
