from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agentos.events import DataClassification
from agentos.memory.in_memory import InMemoryMemoryManager, InMemoryMemoryStore
from agentos.memory.models import (
    ApplyMemoryRetention,
    AuthorizedMemory,
    BoundedMemoryContent,
    GetMemory,
    InvalidateMemory,
    MemoryAccessDenied,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryReference,
    MemoryScope,
    MemoryStatus,
    SaveMemory,
)
from agentos.memory.security import InMemoryMemoryAuthorizationPolicy


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, value=NOW):
        self.value = value

    def now(self):
        return self.value


def context(**overrides):
    values = {
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "agent_id": "agent-1",
        "execution_id": "execution-1",
        "correlation_id": "correlation-1",
        "purpose": "memory.write",
        "actor": "agent-1",
    }
    values.update(overrides)
    return MemoryOperationContext(**values)


def save_command(**overrides):
    values = {
        "context": context(),
        "scope": MemoryScope.PRIVATE,
        "kind": "FACT",
        "content": BoundedMemoryContent("the bounded fact"),
        "provenance": MemoryProvenance(
            source_kind="USER_STATEMENT",
            source_refs=("source:1",),
            authored_by="user-1",
            observed_at=NOW,
            confidence=0.9,
            integrity_ref="integrity:1",
        ),
        "classification": DataClassification.INTERNAL,
        "retention_policy_ref": "retention:1",
        "idempotency_key": "save:1",
        "expires_at": NOW + timedelta(days=1),
    }
    values.update(overrides)
    return SaveMemory(**values)


def reference(memory_id, version, *, purpose="memory.write", agent_id="agent-1", workspace_id="workspace-1"):
    return MemoryReference(
        memory_id=memory_id,
        version=version,
        user_id="user-1",
        workspace_id=workspace_id,
        permitted_agent_id=agent_id,
        authorization_ref=f"owner:{agent_id}",
        purpose=purpose,
        expires_at=NOW + timedelta(minutes=10),
        integrity_ref="integrity:1",
    )


def manager():
    policy = InMemoryMemoryAuthorizationPolicy()
    policy.register_agent("user-1", "agent-1")
    policy.register_workspace_access("user-1", "workspace-1", "agent-1")
    store = InMemoryMemoryStore()
    return InMemoryMemoryManager(store=store, authorization=policy, clock=FakeClock()), store, policy


def test_save_and_get_require_full_context_and_emit_minimal_facts():
    service, store, _ = manager()
    receipt = service.save(save_command())

    authorized = service.get(
        GetMemory(
            context=context(purpose="memory.read"),
            memory_ref=reference(receipt.memory_id, receipt.version, purpose="memory.read"),
            classification_ceiling=DataClassification.INTERNAL,
        )
    )

    assert isinstance(authorized, AuthorizedMemory)
    assert str(authorized.content) == "the bounded fact"
    assert [event.event_type for event in store.outbox] == ["MemorySaved", "MemoryRead"]
    assert all("the bounded fact" not in repr(event) for event in store.outbox)


def test_update_requires_expected_version_and_same_key_is_idempotent():
    service, store, _ = manager()
    first = service.save(save_command())
    ref = reference(first.memory_id, first.version)
    update = save_command(
        memory_ref=ref,
        expected_version=first.version,
        content=BoundedMemoryContent("updated fact"),
        idempotency_key="save:2",
    )
    second = service.save(update)
    replay = service.save(update)

    assert second.version == 2
    assert replay == replace(second, already_applied=True)
    assert len(store.records) == 1
    assert len(store.outbox) == 2

    with pytest.raises(Exception):
        service.save(replace(update, idempotency_key="save:3", expected_version=1))


def test_update_cannot_raise_classification_or_change_memory_scope():
    service, _, _ = manager()
    first = service.save(save_command())
    ref = reference(first.memory_id, first.version)
    restricted_context = context(classification_ceiling=DataClassification.INTERNAL)

    with pytest.raises(MemoryAccessDenied):
        service.save(
            replace(
                save_command(context=restricted_context, memory_ref=ref, expected_version=first.version, idempotency_key="save:restricted"),
                classification=DataClassification.RESTRICTED,
            )
        )

    with pytest.raises(Exception):
        service.save(
            replace(
                save_command(memory_ref=ref, expected_version=first.version, idempotency_key="save:scope"),
                scope=MemoryScope.WORKSPACE,
            )
        )


def test_invalidate_is_versioned_and_cannot_resurrect():
    service, store, _ = manager()
    first = service.save(save_command())
    invalidated = service.invalidate(
        InvalidateMemory(
            context=context(purpose="memory.invalidate"),
            memory_ref=reference(first.memory_id, first.version, purpose="memory.invalidate"),
            expected_version=first.version,
            reason="incorrect",
            idempotency_key="invalidate:1",
        )
    )

    assert invalidated.status is MemoryStatus.INVALIDATED
    with pytest.raises(MemoryAccessDenied):
        service.get(
            GetMemory(
                context=context(purpose="memory.read"),
                memory_ref=reference(first.memory_id, invalidated.version, purpose="memory.read"),
            )
        )
    with pytest.raises(Exception):
        service.save(
            save_command(
                memory_ref=reference(first.memory_id, invalidated.version),
                expected_version=invalidated.version,
                idempotency_key="save:resurrect",
            )
        )
    assert [event.event_type for event in store.outbox] == [
        "MemorySaved", "MemoryInvalidated", "MemoryAccessDenied", "MemoryAccessDenied"
    ]


def test_retention_only_evaluates_explicit_refs_and_returns_auditable_counts():
    service, store, _ = manager()
    expiring = service.save(save_command(idempotency_key="save:expiring", expires_at=NOW - timedelta(minutes=1)))
    retained = service.save(save_command(idempotency_key="save:retained", expires_at=NOW + timedelta(days=1)))
    receipt = service.apply_retention(
        ApplyMemoryRetention(
            context=context(purpose="memory.retention"),
            scope=MemoryScope.PRIVATE,
            memory_refs=(reference(expiring.memory_id, expiring.version, purpose="memory.retention"), reference(retained.memory_id, retained.version, purpose="memory.retention")),
            retention_policy_ref="retention:1",
            policy_cutoff_at=NOW,
            idempotency_key="retention:1",
        )
    )

    assert receipt.evaluated_count == 2
    assert receipt.expired_count == 1
    assert receipt.retained_count == 1
    assert store.get(expiring.memory_id).status is MemoryStatus.EXPIRED
    assert store.get(retained.memory_id).status is MemoryStatus.ACTIVE
