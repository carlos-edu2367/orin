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
from agentos.context.sharing import ResolvedContextSeed, SharedContextReference

from .models import BoundedSearchIntent, MemoryFilter, MemoryOperationContext, MemoryScope, SearchMemory


class MemoryContextSource:
    """Reference-only ContextSource adapter over the public MemoryManager port."""

    source_kind = SourceKind.MEMORY

    def __init__(
        self,
        manager,
        *,
        search_intent: str = "memory",
        maximum_results: int = 20,
        maximum_content_units: int = 512,
        filters: tuple[MemoryFilter, ...] = (),
        shared_service=None,
    ) -> None:
        self._manager = manager
        self._search_intent = BoundedSearchIntent(search_intent)
        if maximum_results < 1 or maximum_results > 100:
            raise ValueError("maximum_results is out of bounds")
        self._maximum_results = maximum_results
        if maximum_content_units < 1 or maximum_content_units > 4096:
            raise ValueError("maximum_content_units is out of bounds")
        self._maximum_content_units = maximum_content_units
        self._filters = tuple(filters)
        self._shared_service = shared_service

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
                filters=self._filters,
                maximum_results=self._maximum_results,
                maximum_content_units=self._maximum_content_units,
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
                        transformation_chain=match.provenance.transformation_chain
                        + tuple(reason.value for reason in match.match_reasons),
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

    def collect_shared(self, query: AuthorizedContextQuery, seed: ResolvedContextSeed) -> tuple[ContextCandidate, ...]:
        if query.context.execution_id != seed.target_execution_id:
            return ()
        if ContextItemKind.MEMORY_REFERENCE not in query.allowed_kinds:
            return ()
        candidates: list[ContextCandidate] = []
        ownership = OwnershipScope(
            user_id=query.context.user_id,
            workspace_id=query.context.workspace_id,
            agent_id=query.context.agent_id,
            execution_id=query.context.execution_id,
        )
        for shared in seed.authorized_candidates:
            if self._shared_service is not None and not self._shared_service.is_reference_current(shared):
                continue
            reference = ContentReference(f"memory:{shared.source_ref}:v{shared.source_version}")
            candidates.append(
                ContextCandidate(
                    candidate_id=f"shared-memory:{shared.shared_ref_id}",
                    kind=ContextItemKind.MEMORY_REFERENCE,
                    content=reference,
                    ownership=ownership,
                    provenance=Provenance(
                        source_kind=SourceKind.MEMORY,
                        source_ref=SourceReference(shared.source_ref),
                        source_version=str(shared.source_version) if shared.source_version is not None else None,
                        retrieved_at=query.cutoff_at,
                        transformation_chain=("shared-reference:v1",),
                    ),
                    classification=shared.classification,
                    relevance=1.0,
                    priority=ContextPriority.NORMAL,
                    estimated_tokens=1,
                    source_version=str(shared.source_version) if shared.source_version is not None else None,
                    integrity_ref=shared.integrity_ref,
                )
            )
        return tuple(candidates)


__all__ = ["MemoryContextSource"]
