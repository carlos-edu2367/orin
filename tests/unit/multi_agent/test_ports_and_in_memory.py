from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentos.events import DataClassification, EventEnvelope
from agentos.multi_agent import (
    Collaboration,
    CollaborationPolicy,
    InMemoryMultiAgentStore,
    MultiAgentEventRecorder,
)


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


def _collaboration() -> Collaboration:
    return Collaboration(
        collaboration_id="collab:1",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="actor:1",
        participant_agent_ids=("agent:source",),
        coordinator_agent_id="agent:source",
        policy=CollaborationPolicy(4, ("task.delegation",), DataClassification.INTERNAL),
        correlation_id="corr:1",
        created_at=NOW,
        version=1,
    )


def test_store_preserves_removed_participant_and_blocks_new_work():
    store = InMemoryMultiAgentStore()
    store.save_collaboration(_collaboration(), idempotency_key="collab:1")
    store.add_participant("collab:1", "agent:target", NOW, idempotency_key="participant:1")
    store.remove_participant("collab:1", "agent:target", NOW, idempotency_key="participant:remove")

    snapshot = store.get_collaboration("collab:1", "user:1", "workspace:1")
    assert snapshot.participant("agent:target").state.value == "REMOVED"
    assert store.can_accept("collab:1", "agent:target") is False


def test_store_deduplicates_event_id_and_rejects_conflicting_payload():
    store = InMemoryMultiAgentStore()
    event = EventEnvelope(
        event_id="event:1",
        event_type="CollaborationCreated",
        event_version=1,
        occurred_at=NOW,
        source="multi-agent",
        correlation_id="corr:1",
        causation_id="command:1",
        sequence=None,
        user_id="user:1",
        workspace_id="workspace:1",
        execution_id=None,
        classification=DataClassification.INTERNAL,
        payload={"collaboration_id": "collab:1"},
    )
    assert isinstance(store, MultiAgentEventRecorder)
    assert store.record_event(event) is True
    assert store.record_event(event) is False
    with pytest.raises(ValueError, match="event_id"):
        store.record_event(replace(event, event_type="DifferentFact"))


def test_store_exposes_commit_unknown_until_inspected():
    store = InMemoryMultiAgentStore()
    transaction = store.begin_commit("tx:1", "key:1", unknown=True)
    assert transaction.commit_state.value == "UNKNOWN"
    assert store.inspect_commit("tx:1", "key:1").commit_state.value == "COMMITTED"
