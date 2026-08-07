from __future__ import annotations

from agentos.context.models import (
    AuthorizedContextQuery,
    ContextCandidate,
    ContextItemKind,
    ContextPriority,
    ContentReference,
    OwnershipScope,
    Provenance,
    SourceKind,
    SourceReference,
)

from .models import BoundedSearchIntent, MemoryOperationContext, MemoryScope, SearchMemory


class MemoryContextSource:
    """Reference-only ContextSource adapter over the public MemoryManager port."""

    source_kind = SourceKind.MEMORY

    def __init__(self, manager, *, search_intent: str = "memory", maximum_results: int = 20) -> None:
        self._manager = manager
        self._search_intent = BoundedSearchIntent(search_intent)
        if maximum_results < 1 or maximum_results > 100:
            raise ValueError("maximum_results is out of bounds")
        self._maximum_results = maximum_results

    def collect(self, query: AuthorizedContextQuery) -> tuple[ContextCandidate, ...]:
        memory_context = MemoryOperationContext(
            user_id=query.context.user_id,
            workspace_id=query.context.workspace_id,
            agent_id=query.context.agent_id,
            execution_id=query.context.execution_id,
            correlation_id=query.context.correlation_id,
            purpose=query.purpose,
            actor=query.context.agent_id,
        )
        result = self._manager.search(
            SearchMemory(
                context=memory_context,
                allowed_scopes=(MemoryScope.PRIVATE, MemoryScope.WORKSPACE, MemoryScope.USER),
                query=self._search_intent,
                maximum_results=self._maximum_results,
                maximum_content_units=512,
                classification_ceiling=query.classification_ceiling,
            )
        )
        if ContextItemKind.MEMORY_REFERENCE not in query.allowed_kinds:
            return ()
        ownership = OwnershipScope(
            user_id=query.context.user_id,
            workspace_id=query.context.workspace_id,
            agent_id=query.context.agent_id,
            execution_id=query.context.execution_id,
        )
        candidates: list[ContextCandidate] = []
        for match in result.matches:
            reference = ContentReference(f"memory:{match.memory_ref.memory_id}:v{match.version}")
            candidates.append(
                ContextCandidate(
                    candidate_id=f"memory:{match.memory_ref.memory_id}:v{match.version}",
                    kind=ContextItemKind.MEMORY_REFERENCE,
                    content=reference,
                    ownership=ownership,
                    provenance=Provenance(
                        source_kind=SourceKind.MEMORY,
                        source_ref=SourceReference(str(match.memory_ref.memory_id)),
                        source_version=str(match.version),
                        authored_by=match.provenance.authored_by,
                        observed_at=match.provenance.observed_at,
                        retrieved_at=query.cutoff_at,
                        transformation_chain=match.provenance.transformation_chain,
                    ),
                    classification=match.classification,
                    relevance=match.relevance,
                    priority=ContextPriority.NORMAL,
                    estimated_tokens=max(1, len(match.excerpt or str(reference)) // 4),
                    created_at=query.cutoff_at,
                    source_version=str(match.version),
                    integrity_ref=match.memory_ref.integrity_ref,
                )
            )
        return tuple(candidates)


__all__ = ["MemoryContextSource"]
