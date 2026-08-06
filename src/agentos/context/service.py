from __future__ import annotations

import re
from dataclasses import replace
from uuid import uuid4

from .models import (
    AuthorizedContextQuery,
    ContextAssemblyRequest,
    ContextBudget,
    ContextCandidate,
    ContextDisposition,
    ContextError,
    ContextErrorCategory,
    ContextItem,
    ContextItemKind,
    ContextManifest,
    ContextManifestReference,
    ContextOperationContext,
    ContextPolicySnapshot,
    ContextPriority,
    ContextReference,
    ContextSnapshot,
    ContextTransformation,
    ContextTurnUpdate,
    ContentReference,
    DataClassification,
    ExcludedItemRecord,
    IncludedItemRecord,
    OwnershipScope,
    Provenance,
    Retryability,
    SourceKind,
    TaskSnapshot,
    TokenAccounting,
    TurnReference,
)


_SECRET_PATTERN = re.compile(
    r"(?ix)(?:bearer\s+[a-z0-9._~+/=-]{8,}|(?:api[_-]?key|password|secret|credential|token)\s*[:=]\s*[^\s,;]+)"
)
_PRIORITY_ORDER = {
    ContextPriority.REQUIRED: 0,
    ContextPriority.HIGH: 1,
    ContextPriority.NORMAL: 2,
    ContextPriority.LOW: 3,
}
_CLASSIFICATION_ORDER = {
    DataClassification.INTERNAL: 0,
    DataClassification.CONFIDENTIAL: 1,
    DataClassification.RESTRICTED: 2,
}
_DATA_KINDS = {
    ContextItemKind.SUMMARY,
    ContextItemKind.MESSAGE,
    ContextItemKind.MEMORY_REFERENCE,
    ContextItemKind.FILE_REFERENCE,
    ContextItemKind.DECISION,
    ContextItemKind.EVENT,
    ContextItemKind.TOOL_RESULT,
}


class ContextManagerService:
    """Deterministic RFC 104 pipeline over injected public ports."""

    def __init__(self, *, sources, recorder, policy, clock, cancellation=None) -> None:
        self._sources = tuple(sources)
        self._recorder = recorder
        self._policy = policy
        self._clock = clock
        self._cancellation = cancellation
        self._active: dict[str, ContextSnapshot] = {}
        self._requests: dict[str, ContextAssemblyRequest] = {}
        self._applied_turns: set[tuple[str, str, int]] = set()
        self._finalized: set[str] = set()

    @property
    def active_executions(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def assemble(self, request: ContextAssemblyRequest) -> ContextSnapshot:
        self._ensure_not_finalized(request.context.execution_id)
        snapshot = self._assemble(request, extra_candidates=(), previous_manifest=None)
        self._active[request.context.execution_id] = snapshot
        self._requests[request.context.execution_id] = request
        return snapshot

    def apply_turn(self, request: ContextTurnUpdate) -> ContextSnapshot:
        self._ensure_not_finalized(request.context.execution_id)
        key = (request.context.execution_id, str(request.previous_manifest_ref), request.expected_turn)
        if key in self._applied_turns:
            raise ContextError(ContextErrorCategory.TURN_CONFLICT, "CONTEXT_TURN_ALREADY_APPLIED")
        try:
            previous = self._recorder.load(
                request.previous_manifest_ref,
                OwnershipScope.from_context(request.context),
            )
        except Exception as exc:
            raise ContextError(ContextErrorCategory.REFERENCE, "CONTEXT_MANIFEST_UNAVAILABLE") from exc
        if previous.execution_id != request.context.execution_id or previous.turn != request.expected_turn:
            raise ContextError(ContextErrorCategory.TURN_CONFLICT, "CONTEXT_EXPECTED_TURN_MISMATCH")
        if previous.ownership is not None and previous.ownership != OwnershipScope.from_context(request.context):
            raise ContextError(ContextErrorCategory.OWNERSHIP, "CONTEXT_MANIFEST_OWNERSHIP_MISMATCH")

        base = self._requests.get(request.context.execution_id)
        if base is None:
            base = ContextAssemblyRequest(
                context=request.context,
                turn=request.expected_turn + 1,
                task=TaskSnapshot(reference=ContextReference(f"manifest-task:{previous.manifest_id}")),
                model_requirements_ref="requirements:recovered",
                budget=ContextBudget(maximum_input_tokens=max(previous.token_accounting.input_tokens, 1)),
                prior_manifest_ref=request.previous_manifest_ref,
            )
        else:
            base = replace(
                base,
                context=request.context,
                turn=request.expected_turn + 1,
                prior_manifest_ref=request.previous_manifest_ref,
            )
        extra = self._turn_candidates(request)
        snapshot = self._assemble(base, extra_candidates=extra, previous_manifest=previous, usage=request.usage)
        self._applied_turns.add(key)
        self._active[request.context.execution_id] = snapshot
        self._requests[request.context.execution_id] = base
        return snapshot

    def finalize(self, execution_id: str, disposition: ContextDisposition) -> None:
        if not isinstance(disposition, ContextDisposition):
            raise ContextError(ContextErrorCategory.INVALID_REQUEST, "CONTEXT_DISPOSITION_INVALID")
        self._active.pop(execution_id, None)
        self._requests.pop(execution_id, None)
        self._finalized.add(execution_id)
        finalize = getattr(self._recorder, "finalize", None)
        if finalize is not None:
            finalize(execution_id, disposition)

    def _assemble(
        self,
        request: ContextAssemblyRequest,
        *,
        extra_candidates: tuple[ContextCandidate, ...],
        previous_manifest: ContextManifest | None,
        usage: TokenAccounting | None = None,
    ) -> ContextSnapshot:
        self._check_cancelled()
        policy = self._policy.resolve(request)
        if request.prior_manifest_ref is not None and previous_manifest is None:
            try:
                previous_manifest = self._recorder.load(
                    request.prior_manifest_ref,
                    OwnershipScope.from_context(request.context),
                )
            except Exception as exc:
                raise ContextError(ContextErrorCategory.REFERENCE, "CONTEXT_PRIOR_MANIFEST_UNAVAILABLE") from exc
            if previous_manifest.execution_id != request.context.execution_id:
                raise ContextError(ContextErrorCategory.OWNERSHIP, "CONTEXT_PRIOR_MANIFEST_SCOPE_MISMATCH")
            if (
                previous_manifest.ownership is not None
                and previous_manifest.ownership != OwnershipScope.from_context(request.context)
            ):
                raise ContextError(ContextErrorCategory.OWNERSHIP, "CONTEXT_PRIOR_MANIFEST_OWNERSHIP_MISMATCH")

        query = AuthorizedContextQuery(
            context=request.context,
            cutoff_at=policy.source_cutoff_at,
            classification_ceiling=policy.classification_ceiling,
            allowed_kinds=tuple(ContextItemKind),
            purpose=request.context.purpose,
        )
        candidates = [self._task_candidate(request, policy)]
        pre_excluded: list[ExcludedItemRecord] = []
        candidates.extend(extra_candidates)
        for source in self._sources:
            self._check_cancelled()
            try:
                source_candidates = source.collect(query)
            except ContextError:
                raise
            except Exception as exc:
                raise ContextError(
                    ContextErrorCategory.REFERENCE,
                    "CONTEXT_SOURCE_UNAVAILABLE",
                    Retryability.POLICY_DEPENDENT,
                ) from exc
            for candidate in source_candidates:
                try:
                    candidates.append(self._prepare_candidate(candidate, request.context, policy))
                except ContextError as error:
                    if (
                        error.category
                        in {
                            ContextErrorCategory.SANITIZATION,
                            ContextErrorCategory.CLASSIFICATION,
                            ContextErrorCategory.INTEGRITY,
                            ContextErrorCategory.PROVENANCE,
                        }
                        and candidate.priority is not ContextPriority.REQUIRED
                    ):
                        pre_excluded.append(
                            ExcludedItemRecord(
                                candidate.candidate_id,
                                candidate.kind,
                                self._exclusion_reason(error.category),
                                self._reference(candidate),
                            )
                        )
                        continue
                    raise
        candidates = self._deduplicate(candidates)
        items, included, excluded, transformations, accounting = self._allocate(
            candidates, request, policy, initial_excluded=tuple(pre_excluded)
        )
        self._check_cancelled()
        if usage is not None:
            accounting = accounting.plus(
                replace(
                    usage,
                    candidate_tokens=0,
                    input_tokens=0,
                    control_tokens=0,
                    excluded_tokens=0,
                    transformed_tokens=0,
                    reserved_output_tokens=0,
                    reserved_control_tokens=0,
                )
            )
        manifest = ContextManifest(
            manifest_id=ContextManifestReference(f"manifest:{uuid4().hex}"),
            execution_id=request.context.execution_id,
            turn=request.turn,
            policy_version=policy.policy_version,
            tokenizer_profile=policy.tokenizer_profile,
            source_cutoff_at=policy.source_cutoff_at,
            included=tuple(included),
            excluded=tuple(excluded),
            transformations=tuple(transformations),
            token_accounting=accounting,
            previous_manifest_id=previous_manifest.manifest_id if previous_manifest else None,
            created_at=self._clock.now(),
            ownership=OwnershipScope.from_context(request.context),
        )
        recorded_ref = self._recorder.record(manifest)
        if not recorded_ref:
            raise ContextError(ContextErrorCategory.RECONCILIATION, "CONTEXT_MANIFEST_REFERENCE_MISSING")
        snapshot = ContextSnapshot(
            execution_id=request.context.execution_id,
            turn=request.turn,
            items=tuple(items),
            token_accounting=accounting,
            context_ref=ContextReference(f"context:{uuid4().hex}"),
            manifest_ref=recorded_ref,
            assembled_at=self._clock.now(),
        )
        return snapshot

    def _task_candidate(self, request: ContextAssemblyRequest, policy: ContextPolicySnapshot) -> ContextCandidate:
        content = request.task.content
        if content is None:
            content = ContentReference(ContextReference(str(request.task.reference)))
        candidate = ContextCandidate(
            candidate_id=f"task:{request.task.reference}",
            kind=ContextItemKind.TASK,
            content=content,
            ownership=OwnershipScope.from_context(request.context),
            provenance=Provenance(
                source_kind=SourceKind.TASK,
                source_ref=str(request.task.reference),
                retrieved_at=self._clock.now(),
            ),
            classification=DataClassification.INTERNAL,
            relevance=1.0,
            priority=ContextPriority.REQUIRED,
            estimated_tokens=request.task.estimated_tokens,
        )
        return self._prepare_candidate(candidate, request.context, policy)

    def _prepare_candidate(
        self,
        candidate: ContextCandidate,
        context: ContextOperationContext,
        policy: ContextPolicySnapshot,
    ) -> ContextCandidate:
        expected = OwnershipScope.from_context(context)
        if candidate.ownership != expected:
            raise ContextError(ContextErrorCategory.OWNERSHIP, "CONTEXT_CANDIDATE_SCOPE_MISMATCH")
        if _CLASSIFICATION_ORDER[candidate.classification] > _CLASSIFICATION_ORDER[policy.classification_ceiling]:
            raise ContextError(ContextErrorCategory.CLASSIFICATION, "CONTEXT_CLASSIFICATION_NOT_ALLOWED")
        if candidate.created_at is not None and candidate.created_at > policy.source_cutoff_at:
            raise ContextError(ContextErrorCategory.INTEGRITY, "CONTEXT_CANDIDATE_AFTER_CUTOFF")
        if candidate.integrity_ref is not None and not str(candidate.integrity_ref).strip():
            raise ContextError(ContextErrorCategory.INTEGRITY, "CONTEXT_INTEGRITY_REFERENCE_INVALID")
        if isinstance(candidate.content, str):
            if _SECRET_PATTERN.search(candidate.content):
                if candidate.priority is ContextPriority.REQUIRED:
                    raise ContextError(ContextErrorCategory.SANITIZATION, "CONTEXT_REQUIRED_CONTENT_UNSAFE")
                raise ContextError(ContextErrorCategory.SANITIZATION, "CONTEXT_CANDIDATE_UNSAFE")
            if len(candidate.content) > policy.max_inline_characters:
                if candidate.priority is ContextPriority.REQUIRED:
                    raise ContextError(ContextErrorCategory.SANITIZATION, "CONTEXT_REQUIRED_CONTENT_TOO_LARGE")
                if candidate.integrity_ref:
                    return replace(
                        candidate,
                        content=ContentReference(ContextReference(str(candidate.integrity_ref))),
                        estimated_tokens=1,
                        provenance=replace(
                            candidate.provenance,
                            transformation_chain=candidate.provenance.transformation_chain
                            + ("reference-substitution:v1",),
                        ),
                    )
                raise ContextError(ContextErrorCategory.SANITIZATION, "CONTEXT_CANDIDATE_TOO_LARGE")
        return candidate

    @staticmethod
    def _deduplicate(candidates: list[ContextCandidate]) -> list[ContextCandidate]:
        result = []
        seen = set()
        for candidate in candidates:
            if str(candidate.candidate_id) in seen:
                continue
            seen.add(str(candidate.candidate_id))
            result.append(candidate)
        return result

    def _allocate(self, candidates, request, policy, *, initial_excluded=()):
        for required_kind in policy.required_kinds:
            if not any(candidate.kind is required_kind for candidate in candidates):
                raise ContextError(ContextErrorCategory.INVALID_REQUEST, "CONTEXT_REQUIRED_KIND_MISSING")
        depths = self._dependency_depths(candidates)
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                _PRIORITY_ORDER[candidate.priority],
                depths[str(candidate.candidate_id)],
                -candidate.relevance,
                candidate.estimated_tokens,
                -(candidate.created_at.timestamp() if candidate.created_at else float("-inf")),
                str(candidate.provenance.source_kind),
                str(candidate.candidate_id),
            ),
        )
        items = []
        included = []
        excluded = list(initial_excluded)
        transformations = []
        per_kind = {}
        used = 0
        excluded_tokens = sum(
            candidate.estimated_tokens
            for candidate in candidates
            if any(record.candidate_id == candidate.candidate_id for record in initial_excluded)
        )
        transformed_tokens = 0
        for candidate in ordered:
            self._check_cancelled()
            limit = request.budget.limit_for(candidate.kind)
            kind_used = per_kind.get(candidate.kind, 0)
            reason = None
            if limit is not None and kind_used + candidate.estimated_tokens > limit:
                reason = "CATEGORY_BUDGET_EXCEEDED"
            elif used + candidate.estimated_tokens > request.budget.available_input_tokens:
                reason = "BUDGET_EXCEEDED"
            if reason is not None:
                if candidate.priority is ContextPriority.REQUIRED:
                    raise ContextError(ContextErrorCategory.BUDGET, "CONTEXT_REQUIRED_ITEM_DOES_NOT_FIT")
                excluded.append(ExcludedItemRecord(candidate.candidate_id, candidate.kind, reason, self._reference(candidate)))
                excluded_tokens += candidate.estimated_tokens
                continue
            provenance = candidate.provenance
            if candidate.kind in _DATA_KINDS:
                provenance = replace(
                    provenance,
                    transformation_chain=provenance.transformation_chain
                    + ("untrusted-data-boundary:v1",),
                )
                transformations.append(
                    ContextTransformation(candidate.candidate_id, "UNTRUSTED_DATA_BOUNDARY")
                )
            item = ContextItem(
                candidate_id=candidate.candidate_id,
                kind=candidate.kind,
                content=candidate.content,
                ownership=candidate.ownership,
                provenance=provenance,
                classification=candidate.classification,
                priority=candidate.priority,
                estimated_tokens=candidate.estimated_tokens,
                untrusted_data=candidate.kind in _DATA_KINDS,
                content_role="DATA" if candidate.kind in _DATA_KINDS else "INSTRUCTION",
            )
            order = len(items)
            items.append(item)
            included.append(
                IncludedItemRecord(
                    candidate_id=candidate.candidate_id,
                    kind=candidate.kind,
                    reference=self._reference(candidate),
                    estimated_tokens=candidate.estimated_tokens,
                    order=order,
                )
            )
            if isinstance(candidate.content, ContentReference):
                item_provenance = replace(
                    item.provenance,
                    transformation_chain=item.provenance.transformation_chain
                    + ("reference-boundary:v1",),
                )
                items[-1] = replace(item, provenance=item_provenance)
                transformations.append(
                    ContextTransformation(candidate.candidate_id, "REFERENCE_BOUNDARY", candidate.content.reference)
                )
                transformed_tokens += candidate.estimated_tokens
            used += candidate.estimated_tokens
            per_kind[candidate.kind] = kind_used + candidate.estimated_tokens
        accounting = TokenAccounting(
            candidate_tokens=sum(candidate.estimated_tokens for candidate in candidates),
            input_tokens=used,
            control_tokens=sum(
                item.estimated_tokens for item in items if item.kind is ContextItemKind.CONTROL_STATE
            ),
            excluded_tokens=excluded_tokens,
            transformed_tokens=transformed_tokens,
            reserved_output_tokens=request.budget.reserved_output_tokens,
            reserved_control_tokens=request.budget.reserved_control_tokens,
        )
        return items, included, excluded, transformations, accounting

    @staticmethod
    def _dependency_depths(candidates):
        by_id = {str(candidate.candidate_id): candidate for candidate in candidates}
        depths = {}

        def depth(candidate_id, visiting=()):
            if candidate_id in depths:
                return depths[candidate_id]
            if candidate_id in visiting or candidate_id not in by_id:
                return 0
            value = 0
            for dependency in by_id[candidate_id].depends_on:
                value = max(value, depth(str(dependency), visiting + (candidate_id,)) + 1)
            depths[candidate_id] = value
            return value

        for candidate in candidates:
            depth(str(candidate.candidate_id))
        return depths

    @staticmethod
    def _exclusion_reason(category: ContextErrorCategory) -> str:
        return {
            ContextErrorCategory.SANITIZATION: "SANITIZATION_FAILED",
            ContextErrorCategory.CLASSIFICATION: "CLASSIFICATION_NOT_ALLOWED",
            ContextErrorCategory.INTEGRITY: "INTEGRITY_FAILED",
            ContextErrorCategory.PROVENANCE: "PROVENANCE_INVALID",
        }.get(category, "CANDIDATE_REJECTED")

    @staticmethod
    def _reference(candidate: ContextCandidate) -> ContextReference:
        if isinstance(candidate.content, ContentReference):
            return candidate.content.reference
        return ContextReference(f"candidate:{candidate.candidate_id}")

    def _turn_candidates(self, request: ContextTurnUpdate) -> tuple[ContextCandidate, ...]:
        references = []
        if request.model_message is not None:
            references.append(request.model_message)
        references.extend(request.tool_results)
        references.extend(request.new_messages)
        references.extend(request.decisions)
        references.extend(request.observed_events)
        if request.control_state is not None:
            references.append(request.control_state)
        ownership = OwnershipScope.from_context(request.context)
        result = []
        for index, reference in enumerate(references):
            kind = reference.kind
            priority = ContextPriority.REQUIRED if kind is ContextItemKind.CONTROL_STATE else ContextPriority.NORMAL
            result.append(
                ContextCandidate(
                    candidate_id=f"turn:{request.expected_turn}:{index}:{reference.reference}",
                    kind=kind,
                    content=ContentReference(ContextReference(str(reference.reference))),
                    ownership=ownership,
                    provenance=Provenance(
                        source_kind=SourceKind.TOOL if kind is ContextItemKind.TOOL_RESULT else SourceKind.USER,
                        source_ref=str(reference.reference),
                        retrieved_at=self._clock.now(),
                    ),
                    classification=reference.classification,
                    relevance=1.0,
                    priority=priority,
                    estimated_tokens=reference.estimated_tokens,
                )
            )
        return tuple(result)

    def _check_cancelled(self) -> None:
        if self._cancellation is not None and self._cancellation.is_cancelled():
            raise ContextError(ContextErrorCategory.CANCELLED, "CONTEXT_ASSEMBLY_CANCELLED")

    def _ensure_not_finalized(self, execution_id: str) -> None:
        if execution_id in self._finalized:
            raise ContextError(ContextErrorCategory.FINALIZED, "CONTEXT_EXECUTION_FINALIZED")


__all__ = ["ContextManagerService"]
