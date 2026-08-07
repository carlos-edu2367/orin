from datetime import datetime, timedelta, timezone

import pytest

from agentos.events import DataClassification
from agentos.memory.in_memory import InMemoryMemoryManager, InMemoryMemoryStore
from agentos.memory.models import (
    BoundedMemoryContent,
    BoundedSearchIntent,
    ConsolidateMemory,
    MemoryAccessDenied,
    MemoryFilter,
    MemoryKind,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryReference,
    MemoryScope,
    MemoryStatus,
    SaveMemory,
    SearchMemory,
)
from agentos.memory.security import InMemoryMemoryAuthorizationPolicy


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def now(self):
        return NOW


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


def manager():
    policy = InMemoryMemoryAuthorizationPolicy()
    policy.register_agent("user-1", "agent-1")
    policy.register_agent("user-1", "agent-2")
    policy.register_workspace_access("user-1", "workspace-1", "agent-1")
    policy.register_workspace_access("user-1", "workspace-1", "agent-2")
    store = InMemoryMemoryStore()
    return InMemoryMemoryManager(store=store, authorization=policy, clock=FakeClock()), store


def save(service, *, key, content, classification=DataClassification.INTERNAL, kind=MemoryKind.FACT):
    return service.save(
        SaveMemory(
            context=context(),
            scope=MemoryScope.PRIVATE,
            kind=kind,
            content=BoundedMemoryContent(content),
            provenance=MemoryProvenance(
                source_kind="USER_STATEMENT",
                source_refs=(f"source:{key}",),
                authored_by="user-1",
                observed_at=NOW,
                confidence=0.9,
                integrity_ref=f"integrity:{key}",
            ),
            classification=classification,
            retention_policy_ref="retention:1",
            idempotency_key=key,
            expires_at=NOW + timedelta(days=1),
        )
    )


def ref(memory_id, *, version=1, purpose="memory.consolidate"):
    return MemoryReference(
        memory_id=memory_id,
        version=version,
        user_id="user-1",
        workspace_id="workspace-1",
        permitted_agent_id="agent-1",
        authorization_ref="owner:agent-1",
        purpose=purpose,
        expires_at=NOW + timedelta(minutes=5),
        integrity_ref="integrity:ref",
    )


def test_search_filters_before_ranking_and_returns_bounded_authorized_matches():
    service, store = manager()
    first = save(service, key="save:1", content="The project deadline is Friday")
    save(service, key="save:2", content="A different unrelated note", classification=DataClassification.RESTRICTED)

    result = service.search(
        SearchMemory(
            context=context(purpose="memory.search"),
            allowed_scopes=(MemoryScope.PRIVATE,),
            query=BoundedSearchIntent("deadline"),
            filters=(MemoryFilter(classification_ceiling=DataClassification.INTERNAL),),
            maximum_results=10,
            maximum_content_units=20,
            classification_ceiling=DataClassification.INTERNAL,
        )
    )

    assert [match.memory_ref.memory_id for match in result.matches] == [first.memory_id]
    assert result.matches[0].excerpt == "The project deadline"
    assert result.matches[0].relevance > 0
    assert len(result.matches[0].excerpt) <= 512
    assert store.outbox[-1].event_type == "MemorySearched"
    assert "deadline" not in repr(store.outbox[-1])


def test_cross_agent_search_fails_closed_without_private_grant():
    service, _ = manager()
    first = save(service, key="save:1", content="private detail")
    with pytest.raises(MemoryAccessDenied):
        service.get(
            __import__("agentos.memory.models", fromlist=["GetMemory"]).GetMemory(
                context=context(agent_id="agent-2", actor="agent-2", purpose="memory.read"),
                memory_ref=ref(first.memory_id, purpose="memory.read"),
            )
        )


def test_consolidation_preserves_lineage_and_strictest_classification_without_mutating_sources():
    service, store = manager()
    first = save(service, key="save:1", content="first fact", classification=DataClassification.INTERNAL)
    second = save(service, key="save:2", content="second fact", classification=DataClassification.CONFIDENTIAL)
    command = ConsolidateMemory(
        context=context(purpose="memory.consolidate"),
        source_refs=(ref(first.memory_id), ref(second.memory_id)),
        target_scope=MemoryScope.PRIVATE,
        target_kind=MemoryKind.SEMANTIC,
        content=BoundedMemoryContent("combined fact"),
        provenance=MemoryProvenance(
            source_kind="CONSOLIDATION",
            source_refs=(str(first.memory_id), str(second.memory_id)),
            authored_by="agent-1",
            observed_at=NOW,
            confidence=0.7,
            transformation_chain=("transform:consolidate",),
            integrity_ref="integrity:combined",
        ),
        retention_policy_ref="retention:1",
        idempotency_key="consolidate:1",
    )

    receipt = service.consolidate(command)
    output = store.get(receipt.output_memory_id)

    assert receipt.status == "CONSOLIDATED"
    assert output.kind is MemoryKind.SEMANTIC
    assert output.classification is DataClassification.CONFIDENTIAL
    assert {item.memory_id for item in output.lineage} == {first.memory_id, second.memory_id}
    assert store.get(first.memory_id).status is MemoryStatus.ACTIVE
    assert store.get(second.memory_id).status is MemoryStatus.ACTIVE
    assert store.outbox[-1].event_type == "MemoryConsolidated"


def test_consolidation_rejects_scope_promotion_before_output_commit():
    service, store = manager()
    first = save(service, key="save:1", content="private fact")
    before = (len(store.records), len(store.audit_log), len(store.outbox))
    command = ConsolidateMemory(
        context=context(purpose="memory.consolidate"),
        source_refs=(ref(first.memory_id),),
        target_scope=MemoryScope.WORKSPACE,
        target_kind=MemoryKind.FACT,
        content=BoundedMemoryContent("promoted fact"),
        provenance=MemoryProvenance(source_kind="CONSOLIDATION", source_refs=("source:combined",), integrity_ref="integrity:combined"),
        retention_policy_ref="retention:1",
        idempotency_key="consolidate:invalid",
    )

    with pytest.raises(Exception):
        service.consolidate(command)
    assert (len(store.records), len(store.audit_log), len(store.outbox)) == before


def test_consolidation_can_atomically_supersede_explicit_sources():
    service, store = manager()
    first = save(service, key="save:1", content="first fact")
    second = save(service, key="save:2", content="second fact")
    command = ConsolidateMemory(
        context=context(purpose="memory.consolidate"),
        source_refs=(ref(first.memory_id), ref(second.memory_id)),
        target_scope=MemoryScope.PRIVATE,
        target_kind=MemoryKind.SEMANTIC,
        content=BoundedMemoryContent("combined fact"),
        provenance=MemoryProvenance(source_kind="CONSOLIDATION", source_refs=("source:combined",), integrity_ref="integrity:combined"),
        retention_policy_ref="retention:1",
        idempotency_key="consolidate:supersede",
        supersede_sources=True,
    )

    receipt = service.consolidate(command)

    assert receipt.status == "CONSOLIDATED"
    assert store.get(first.memory_id).status is MemoryStatus.SUPERSEDED
    assert store.get(second.memory_id).status is MemoryStatus.SUPERSEDED
    assert [event.event_type for event in store.outbox[-2:]] == ["MemoryConsolidated", "MemorySuperseded"]
