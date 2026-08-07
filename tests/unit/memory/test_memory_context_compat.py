from datetime import datetime, timezone

from agentos.context.models import (
    AuthorizedContextQuery,
    ContextItemKind,
    ContextOperationContext,
    ContentReference,
    DataClassification,
)
from agentos.memory.context_compat import MemoryContextSource
from agentos.memory.models import (
    BoundedMemoryContent,
    MemoryKind,
    MemoryFilter,
    MemoryMatch,
    MemoryMatchReason,
    MemoryReference,
    MemoryScope,
    MemorySearchResult,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def context():
    return ContextOperationContext(
        user_id="user-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        purpose="context.memory",
    )


class FakeManager:
    def __init__(self, result):
        self.result = result
        self.queries = []
        self.save_calls = 0

    def search(self, query):
        self.queries.append(query)
        return self.result

    def save(self, command):
        self.save_calls += 1
        raise AssertionError("Context collection must never save Memory")


def result():
    ref = MemoryReference(
        memory_id="memory:1",
        version=2,
        user_id="user-1",
        workspace_id="workspace-1",
        permitted_agent_id="agent-1",
        authorization_ref="owner:agent-1",
        purpose="context.memory",
        expires_at=None,
        integrity_ref="integrity:1",
    )
    return MemorySearchResult(
        matches=(
            MemoryMatch(
                memory_ref=ref,
                version=2,
                kind=MemoryKind.FACT,
                scope=MemoryScope.PRIVATE,
                excerpt="minimal excerpt",
                relevance=1.0,
                match_reasons=(MemoryMatchReason.TERM_MATCH,),
                provenance=__import__("agentos.memory.models", fromlist=["MemoryProvenance"]).MemoryProvenance(
                    source_kind="USER_STATEMENT",
                    source_refs=("source:1",),
                    integrity_ref="integrity:1",
                ),
                classification=DataClassification.INTERNAL,
            ),
        ),
        applied_scope=(MemoryScope.PRIVATE,),
        policy_version="memory-policy:1",
        truncated=False,
        correlation_id="correlation-1",
    )


def test_context_source_propagates_scope_and_returns_reference_first_candidates():
    manager = FakeManager(result())
    source = MemoryContextSource(manager, search_intent="deadline", maximum_results=3)
    query = AuthorizedContextQuery(
        context=context(),
        cutoff_at=NOW,
        classification_ceiling=DataClassification.INTERNAL,
        allowed_kinds=(ContextItemKind.MEMORY_REFERENCE,),
        purpose="context.memory",
    )

    candidates = source.collect(query)

    assert source.source_kind.value == "MEMORY"
    assert len(candidates) == 1
    assert isinstance(candidates[0].content, ContentReference)
    assert candidates[0].provenance.source_kind.value == "MEMORY"


def test_context_source_propagates_bounded_search_filters_and_citation_reasons():
    manager = FakeManager(result())
    source = MemoryContextSource(
        manager,
        search_intent="deadline",
        maximum_results=3,
        filters=(MemoryFilter(kinds=(MemoryKind.FACT,)),),
    )
    query = AuthorizedContextQuery(
        context=context(),
        purpose="memory.search",
        allowed_kinds=(ContextItemKind.MEMORY_REFERENCE,),
        classification_ceiling=DataClassification.INTERNAL,
        cutoff_at=NOW,
    )

    candidates = source.collect(query)

    assert candidates
    assert str(candidates[0].content.reference).startswith("memory:")
    assert candidates[0].provenance.source_version is not None
    assert "TERM_MATCH" in candidates[0].provenance.transformation_chain
    assert candidates[0].classification is DataClassification.INTERNAL
    assert manager.queries[0].context.user_id == "user-1"
    assert manager.queries[0].context.execution_id == "execution-1"
    assert manager.queries[0].context.purpose == "memory.search"


def test_context_collection_has_no_implicit_memory_write():
    manager = FakeManager(result())
    source = MemoryContextSource(manager, search_intent="deadline")
    query = AuthorizedContextQuery(
        context=context(),
        cutoff_at=NOW,
        classification_ceiling=DataClassification.INTERNAL,
        allowed_kinds=(ContextItemKind.MEMORY_REFERENCE,),
        purpose="context.memory",
    )

    source.collect(query)
    assert manager.save_calls == 0
