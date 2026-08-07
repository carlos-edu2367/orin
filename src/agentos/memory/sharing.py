from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from itertools import count
from threading import RLock

from agentos.context.sharing import (
    AuthorizedSourceReference,
    AuthorizeContextShare,
    ContextShareGrant,
    ContextShareStatus,
    CreateSharedContextReference,
    CreateStructuredHandoff,
    ExpireContextShare,
    ExpirationReceipt,
    HandoffRef,
    ResolvedContextSeed,
    ResolveSharedContext,
    RevocationReceipt,
    RevokeContextShare,
    SharedContextExclusion,
    SharedContextKind,
    SharedContextReference,
    StructuredHandoff,
)
from agentos.events.models import DataClassification, EventEnvelope

from .models import (
    GetMemory,
    MemoryAccessDenied,
    MemoryArtifactReference,
    MemoryGrant,
    MemoryOperationContext,
    MemoryReference,
    MemoryStatus,
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class InMemoryMemorySharingService:
    """Reference RFC 303 adapter for Memory references; process-local only."""

    def __init__(self, *, manager, clock=None, event_recorder=None, policy_version: str = "memory-share-policy:1"):
        self.manager = manager
        self.clock = clock or _SystemClock()
        self.event_recorder = event_recorder
        self.policy_version = policy_version
        self._grants: dict[str, ContextShareGrant] = {}
        self._references: dict[str, SharedContextReference] = {}
        self._source_references: dict[str, MemoryReference] = {}
        self._handoffs: dict[str, StructuredHandoff] = {}
        self._idempotency: dict[tuple[str, str], object] = {}
        self._resolution_results: dict[str, ResolvedContextSeed] = {}
        self._events: list[EventEnvelope] = []
        self._sequence: dict[str, int] = {}
        self._ids = count(1)
        self._lock = RLock()

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        with self._lock:
            return tuple(self._events)

    def is_reference_current(self, reference: SharedContextReference) -> bool:
        with self._lock:
            grant = self._grants.get(reference.grant_id)
            if grant is None or grant.status in {ContextShareStatus.REVOKED, ContextShareStatus.EXPIRED, ContextShareStatus.CANCELLED}:
                return False
            if self.clock.now() >= min(grant.expires_at, reference.expires_at):
                return False
            source_ref = self._source_references.get(reference.shared_ref_id)
            if source_ref is None:
                return False
            current = self.manager.store.get(str(source_ref.memory_id))
            return current is not None and current.status is MemoryStatus.ACTIVE and int(current.version) == int(reference.source_version or 0)

    def authorize(self, command: AuthorizeContextShare) -> ContextShareGrant:
        with self._lock:
            existing = self._idempotency.get(("authorize", command.idempotency_key))
            if existing is not None:
                if not isinstance(existing, ContextShareGrant):
                    raise MemoryAccessDenied("IDEMPOTENCY_CONFLICT")
                return existing
            self._require_active(command.user_id, command.source_agent_id)
            self._require_active(command.user_id, command.target_agent_id)
            if command.source_agent_id == command.target_agent_id:
                raise MemoryAccessDenied("SHARE_TARGET_INVALID")
            if command.workspace_id is not None:
                self._require_workspace(command.user_id, command.workspace_id, command.target_agent_id)
            if command.expires_at <= self.clock.now():
                raise MemoryAccessDenied("SHARE_EXPIRED")
            grant = ContextShareGrant(
                grant_id=f"grant:memory:{next(self._ids)}",
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                source_agent_id=command.source_agent_id,
                target_agent_id=command.target_agent_id,
                source_execution_id=command.source_execution_id,
                target_execution_id=command.target_execution_id,
                purpose=command.purpose,
                allowed_kinds=command.requested_kinds,
                filters=command.filters,
                classification_ceiling=command.classification_ceiling,
                budget=command.budget,
                redelegation=False,
                consumption_policy=command.consumption_policy,
                authorization_ref=command.authorization_ref,
                authorization_basis_ref=command.authorization_ref,
                issued_by=command.actor,
                correlation_id=command.correlation_id,
                issued_at=self.clock.now(),
                expires_at=command.expires_at,
            )
            self._grants[grant.grant_id] = grant
            self._idempotency[("authorize", command.idempotency_key)] = grant
            self._emit("ContextShareAuthorized", command, {"grant_id": grant.grant_id, "status": grant.status.value})
            return grant

    def create_reference(self, command: CreateSharedContextReference) -> SharedContextReference:
        with self._lock:
            existing = self._idempotency.get(("reference", command.idempotency_key))
            if existing is not None:
                return existing
            grant = self._grant_for(command.grant_id)
            self._validate_source_scope(grant, command)
            if command.source_kind not in grant.allowed_kinds:
                raise MemoryAccessDenied("SHARE_KIND_DENIED")
            if command.purpose != grant.purpose:
                raise MemoryAccessDenied("SHARE_PURPOSE_DENIED")
            source_ref = self._as_memory_reference(command.source_ref, command.purpose)
            if isinstance(command.source_ref, AuthorizedSourceReference) and command.purpose not in command.source_ref.permitted_purposes:
                raise MemoryAccessDenied("SOURCE_PURPOSE_DENIED")
            if command.expected_source_version is not None and source_ref.version != command.expected_source_version:
                raise MemoryAccessDenied("SOURCE_VERSION_MISMATCH")
            source_context = MemoryOperationContext(
                user_id=source_ref.user_id,
                workspace_id=source_ref.workspace_id,
                agent_id=command.source_agent_id,
                execution_id=command.source_execution_id,
                correlation_id=command.correlation_id,
                purpose=command.purpose,
                actor=command.actor,
            )
            authorized = self.manager.get(GetMemory(context=source_context, memory_ref=source_ref, classification_ceiling=grant.classification_ceiling))
            if authorized.classification.value not in {"INTERNAL", "CONFIDENTIAL", "RESTRICTED"}:
                raise MemoryAccessDenied("CLASSIFICATION_DENIED")
            if not _allows(grant.classification_ceiling, authorized.classification):
                raise MemoryAccessDenied("CLASSIFICATION_DENIED")
            shared_ref_id = f"shared:memory:{next(self._ids)}"
            expires_at = min(grant.expires_at, source_ref.expires_at or grant.expires_at)
            shared = SharedContextReference(
                shared_ref_id=shared_ref_id,
                grant_id=grant.grant_id,
                source_kind=command.source_kind,
                source_ref=str(source_ref.memory_id),
                source_version=int(source_ref.version),
                source_user_id=str(source_ref.user_id),
                source_workspace_id=source_ref.workspace_id,
                source_agent_id=command.source_agent_id,
                target_agent_id=grant.target_agent_id,
                target_execution_id=grant.target_execution_id,
                purpose=grant.purpose,
                classification=authorized.classification,
                integrity_ref=source_ref.integrity_ref,
                created_at=self.clock.now(),
                expires_at=expires_at,
            )
            shared.validate_against(grant, now=self.clock.now())
            self._references[shared.shared_ref_id] = shared
            self._source_references[shared.shared_ref_id] = source_ref
            self._idempotency[("reference", command.idempotency_key)] = shared
            self._emit("SharedContextReferenceCreated", command, {"grant_id": grant.grant_id, "shared_ref_id": shared.shared_ref_id, "source_version": shared.source_version})
            return shared

    def create_handoff(self, command: CreateStructuredHandoff) -> HandoffRef:
        with self._lock:
            existing = self._idempotency.get(("handoff", command.idempotency_key))
            if existing is not None:
                return existing
            grant = self._grant_for(command.grant_id)
            self._validate_command_scope(grant, command.user_id, command.workspace_id, command.source_agent_id, command.target_agent_id, command.source_execution_id, command.target_execution_id)
            if not command.context_refs or len(command.context_refs) > command.budget.maximum_references:
                raise MemoryAccessDenied("SHARE_BUDGET_EXCEEDED")
            for ref in command.context_refs:
                ref.validate_against(grant, now=self.clock.now())
                if ref.shared_ref_id not in self._references:
                    raise MemoryAccessDenied("SHARE_REFERENCE_UNKNOWN")
            expires_at = min((ref.expires_at for ref in command.context_refs), default=grant.expires_at)
            expires_at = min(expires_at, grant.expires_at)
            handoff_id = f"handoff:memory:{next(self._ids)}"
            expected_output = command.expected_output
            if expected_output is None:
                from agentos.context.sharing import OutputContractRef

                expected_output = OutputContractRef(
                    output_contract_id=f"output:{handoff_id}",
                    version=1,
                    expected_kind="REPORT",
                    schema_ref=None,
                    authorization_ref=grant.authorization_ref,
                    integrity_ref=f"integrity:output:{handoff_id}",
                )
            handoff = StructuredHandoff(
                handoff_id=handoff_id,
                grant_id=grant.grant_id,
                user_id=grant.user_id,
                workspace_id=grant.workspace_id,
                from_agent_id=grant.source_agent_id,
                to_agent_id=grant.target_agent_id,
                source_execution_id=grant.source_execution_id,
                target_execution_id=grant.target_execution_id,
                objective=command.objective,
                success_criteria=command.success_criteria,
                constraints=command.constraints,
                expected_output=expected_output,
                context_refs=command.context_refs,
                minimal_snapshot_ref=command.minimal_snapshot_ref,
                delegated_grant_refs=command.delegated_grant_refs,
                budget=command.budget,
                purpose=command.purpose,
                classification=grant.classification_ceiling,
                correlation_id=command.correlation_id,
                version=1,
                integrity_ref=f"integrity:handoff:{handoff_id}",
                created_at=self.clock.now(),
                expires_at=expires_at,
            )
            ref = HandoffRef(
                handoff_id=handoff.handoff_id,
                grant_id=handoff.grant_id,
                from_agent_id=handoff.from_agent_id,
                to_agent_id=handoff.to_agent_id,
                source_execution_id=handoff.source_execution_id,
                target_execution_id=handoff.target_execution_id,
                purpose=handoff.purpose,
                classification=handoff.classification,
                version=handoff.version,
                expires_at=handoff.expires_at,
                integrity_ref=handoff.integrity_ref,
            )
            self._handoffs[handoff.handoff_id] = handoff
            self._idempotency[("handoff", command.idempotency_key)] = ref
            self._emit("StructuredHandoffCreated", command, {"grant_id": grant.grant_id, "handoff_id": handoff.handoff_id, "reference_count": len(handoff.context_refs)})
            return ref

    def resolve(self, query: ResolveSharedContext) -> ResolvedContextSeed:
        with self._lock:
            previous = self._resolution_results.get(query.idempotency_key)
            if previous is not None:
                return previous
            grant = self._grant_for(query.grant_id)
            self._validate_command_scope(grant, query.user_id, query.workspace_id, query.source_agent_id, query.target_agent_id, query.source_execution_id, query.target_execution_id)
            handoff = self._handoffs.get(query.handoff_ref.handoff_id)
            if handoff is None or query.handoff_ref.version != handoff.version or query.handoff_ref.integrity_ref != handoff.integrity_ref:
                raise MemoryAccessDenied("HANDOFF_UNAVAILABLE")
            if query.purpose != grant.purpose or query.handoff_ref.grant_id != grant.grant_id:
                raise MemoryAccessDenied("SHARE_PURPOSE_DENIED")
            self._validate_grant_usable(grant)
            if query.expected_resolution_count != grant.resolution_count:
                raise MemoryAccessDenied("RESOLUTION_COUNT_CONFLICT")
            if grant.consumption_policy == "SINGLE_USE" and grant.status is ContextShareStatus.CONSUMED:
                raise MemoryAccessDenied("SHARE_CONSUMED")
            if len(query.requested_ref_ids) > query.remaining_budget.maximum_references:
                raise MemoryAccessDenied("SHARE_BUDGET_EXCEEDED")
            candidates: list[SharedContextReference] = []
            excluded: list[SharedContextExclusion] = []
            for ref_id in tuple(dict.fromkeys(query.requested_ref_ids)):
                shared = self._references.get(ref_id)
                if shared is None or shared.grant_id != grant.grant_id:
                    excluded.append(SharedContextExclusion(ref_id, SharedContextKind.MEMORY.value, False, "NOT_AUTHORIZED", None))
                    continue
                try:
                    shared.validate_against(grant, now=self.clock.now())
                    source_ref = self._source_references[shared.shared_ref_id]
                    source_context = MemoryOperationContext(
                        user_id=shared.source_user_id,
                        workspace_id=shared.source_workspace_id,
                        agent_id=shared.source_agent_id,
                        execution_id=grant.source_execution_id,
                        correlation_id=f"{query.correlation_id}:resolve:{query.idempotency_key}",
                        purpose=grant.purpose,
                        actor=shared.source_agent_id,
                    )
                    authorized = self.manager.get(GetMemory(context=source_context, memory_ref=source_ref, classification_ceiling=grant.classification_ceiling))
                    if authorized.version != shared.source_version:
                        raise MemoryAccessDenied("VERSION_MISMATCH")
                    candidates.append(shared)
                except MemoryAccessDenied as error:
                    reason = getattr(error, "code", "SOURCE_UNAVAILABLE")
                    excluded.append(SharedContextExclusion(shared.shared_ref_id, shared.source_kind, False, reason, shared.source_version))
            if not candidates:
                raise MemoryAccessDenied("NO_AUTHORIZED_SHARED_CONTEXT")
            updated = replace(
                grant,
                status=ContextShareStatus.CONSUMED,
                consumed_at=self.clock.now(),
                resolution_count=grant.resolution_count + 1,
            )
            self._grants[grant.grant_id] = updated
            result = ResolvedContextSeed(
                grant_id=grant.grant_id,
                target_execution_id=grant.target_execution_id,
                authorized_candidates=tuple(candidates),
                excluded=tuple(excluded),
                policy_version=self.policy_version,
                grant_status=updated.status,
                resolution_count=updated.resolution_count,
                truncated=bool(excluded),
                correlation_id=query.correlation_id,
            )
            self._resolution_results[query.idempotency_key] = result
            self._emit("SharedContextResolved", query, {"grant_id": grant.grant_id, "resolved_count": len(candidates), "excluded_count": len(excluded), "resolution_count": updated.resolution_count})
            self._emit("SharedContextConsumed", query, {"grant_id": grant.grant_id, "resolution_count": updated.resolution_count})
            return result

    def revoke(self, command: RevokeContextShare) -> RevocationReceipt:
        return self._terminalize(command, ContextShareStatus.REVOKED, "ContextShareRevoked")

    def expire(self, command: ExpireContextShare) -> ExpirationReceipt:
        result = self._terminalize(command, ContextShareStatus.EXPIRED, "ContextShareExpired")
        return ExpirationReceipt(result.grant_id, result.previous_status, result.status, result.target_execution_id, result.correlation_id)

    def _terminalize(self, command, status: ContextShareStatus, event_type: str) -> RevocationReceipt:
        with self._lock:
            existing = self._idempotency.get((event_type, command.idempotency_key))
            if existing is not None:
                return existing
            grant = self._grant_for(command.grant_id)
            self._validate_command_scope(grant, command.user_id, command.workspace_id, command.source_agent_id, command.target_agent_id, command.source_execution_id, command.target_execution_id)
            if command.purpose != grant.purpose:
                raise MemoryAccessDenied("SHARE_PURPOSE_DENIED")
            previous = grant.status
            if previous not in {ContextShareStatus.REVOKED, ContextShareStatus.EXPIRED}:
                grant = replace(grant, status=status, revoked_at=self.clock.now() if status is ContextShareStatus.REVOKED else grant.revoked_at)
                self._grants[grant.grant_id] = grant
            receipt = RevocationReceipt(grant.grant_id, previous, grant.status, grant.target_execution_id, command.correlation_id)
            self._idempotency[(event_type, command.idempotency_key)] = receipt
            self._emit(event_type, command, {"grant_id": grant.grant_id, "previous_status": previous.value, "status": grant.status.value})
            return receipt

    def _grant_for(self, grant_id: str) -> ContextShareGrant:
        grant = self._grants.get(str(grant_id))
        if grant is None:
            raise MemoryAccessDenied("SHARE_UNAVAILABLE")
        return grant

    def _validate_grant_usable(self, grant: ContextShareGrant) -> None:
        if self.clock.now() >= grant.expires_at:
            self._grants[grant.grant_id] = replace(grant, status=ContextShareStatus.EXPIRED)
            raise MemoryAccessDenied("SHARE_EXPIRED")
        if grant.status in {ContextShareStatus.REVOKED, ContextShareStatus.EXPIRED, ContextShareStatus.CANCELLED}:
            raise MemoryAccessDenied("SHARE_UNAVAILABLE")

    def _validate_source_scope(self, grant: ContextShareGrant, command: CreateSharedContextReference) -> None:
        self._validate_command_scope(grant, command.user_id, command.workspace_id, command.source_agent_id, command.target_agent_id, command.source_execution_id, command.target_execution_id)
        self._validate_grant_usable(grant)

    @staticmethod
    def _validate_command_scope(grant, user_id, workspace_id, source_agent_id, target_agent_id, source_execution_id, target_execution_id) -> None:
        if (user_id, workspace_id, source_agent_id, target_agent_id, source_execution_id, target_execution_id) != (
            grant.user_id, grant.workspace_id, grant.source_agent_id, grant.target_agent_id, grant.source_execution_id, grant.target_execution_id
        ):
            raise MemoryAccessDenied("SHARE_SCOPE_MISMATCH")

    def _require_active(self, user_id: str, agent_id: str) -> None:
        if not getattr(self.manager.authorization, "is_agent_active")(user_id, agent_id):
            raise MemoryAccessDenied("SHARE_AGENT_INACTIVE")

    def _require_workspace(self, user_id: str, workspace_id: str, agent_id: str) -> None:
        if not getattr(self.manager.authorization, "has_workspace_access")(user_id, workspace_id, agent_id):
            raise MemoryAccessDenied("SHARE_WORKSPACE_DENIED")

    @staticmethod
    def _as_memory_reference(source_ref, purpose: str) -> MemoryReference:
        if isinstance(source_ref, MemoryReference):
            if source_ref.purpose != purpose:
                raise MemoryAccessDenied("SOURCE_PURPOSE_DENIED")
            return source_ref
        if not isinstance(source_ref, AuthorizedSourceReference):
            raise MemoryAccessDenied("SOURCE_REFERENCE_INVALID")
        if source_ref.source_kind != SharedContextKind.MEMORY.value:
            raise MemoryAccessDenied("SOURCE_KIND_INVALID")
        if source_ref.source_version is None:
            raise MemoryAccessDenied("SOURCE_REFERENCE_INVALID")
        return MemoryReference(
            memory_id=source_ref.source_ref,
            version=source_ref.source_version,
            user_id=source_ref.user_id,
            workspace_id=source_ref.workspace_id,
            permitted_agent_id=source_ref.owner_agent_id,
            authorization_ref=source_ref.authorization_ref,
            purpose=purpose,
            expires_at=source_ref.expires_at,
            integrity_ref=source_ref.integrity_ref or "integrity:memory",
        )

    def _emit(self, event_type: str, command, payload: dict[str, object]) -> None:
        execution_id = getattr(command, "execution_id", None)
        sequence = self._sequence.get(str(execution_id), 0) + 1 if execution_id else None
        if execution_id:
            self._sequence[str(execution_id)] = sequence
        event = EventEnvelope(
            event_id=f"memory-share:{event_type}:{getattr(command, 'idempotency_key', next(self._ids))}",
            event_type=event_type,
            event_version=1,
            occurred_at=self.clock.now(),
            source="memory-sharing",
            correlation_id=command.correlation_id,
            causation_id=None,
            sequence=sequence,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            execution_id=execution_id,
            classification=DataClassification.INTERNAL,
            payload=payload,
            agent_id=getattr(command, "actor", None),
        )
        self._events.append(event)
        if self.event_recorder is not None:
            self.event_recorder.record_event(event)


def _allows(ceiling: DataClassification, value: DataClassification) -> bool:
    order = {DataClassification.INTERNAL: 0, DataClassification.CONFIDENTIAL: 1, DataClassification.RESTRICTED: 2}
    return order[DataClassification(ceiling)] >= order[DataClassification(value)]


__all__ = ["InMemoryMemorySharingService"]
