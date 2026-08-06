from datetime import datetime, timezone

import pytest

from agentos.events import DataClassification, EventEnvelope


@pytest.fixture
def event_factory():
    def make(**overrides):
        values = {
            "event_id": "event:1",
            "event_type": "ExecutionStarted",
            "event_version": 1,
            "occurred_at": datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
            "source": "execution",
            "correlation_id": "correlation:1",
            "causation_id": "command:1",
            "sequence": 1,
            "user_id": "user:1",
            "workspace_id": "workspace:1",
            "agent_id": "agent:1",
            "execution_id": "execution:1",
            "classification": DataClassification.INTERNAL,
            "payload": {"state_version": 2},
        }
        values.update(overrides)
        return EventEnvelope(**values)

    return make


@pytest.fixture
def naive_datetime():
    return datetime(2026, 8, 6, 12, 0)
