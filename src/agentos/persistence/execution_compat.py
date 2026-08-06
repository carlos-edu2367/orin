from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from agentos.events.compat import from_execution_event, to_canonical_event
from agentos.events.models import DataClassification
from agentos.execution.models import (
    CancellationReason,
    CancellationReasonCode,
    Execution,
    ExecutionFailure,
    ExecutionLimits,
    ExecutionResult,
    ExecutionState,
    ExecutionUsage,
    FailureReason,
    Ownership,
    TaskSnapshot,
)
from agentos.execution.ports import (
    Accepted,
    AlreadyApplied,
    Conflict,
    ExecutionCommandContext,
    ExecutionDomainChange,
    ExecutionNotFoundError,
    IdempotencyRecord,
    Indeterminate,
    Rejected,
    RejectionReason,
    TransactionCommitState,
    TransactionCommitted as LegacyTransactionCommitted,
    TransactionConflicted as LegacyTransactionConflicted,
    TransactionIndeterminate as LegacyTransactionIndeterminate,
    TransactionReceipt as LegacyTransactionReceipt,
    TransactionRejected as LegacyTransactionRejected,
    TransactionRequest as LegacyTransactionRequest,
    TransactionResult as LegacyTransactionResult,
    TransactionalPersistence as LegacyPersistence,
    UnauthorizedExecutionError,
)
from agentos.persistence.models import (
    AuthorizedRead,
    AuthorizedRecord,
    CommitState,
    ExpectedVersion,
    InspectCommit,
    OutboxChange,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionCommitted,
    TransactionConflicted,
    TransactionIndeterminate,
    TransactionOptions,
    TransactionRejected,
    TransactionRequest,
)
from agentos.persistence.ports import TransactionalPersistence


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _read_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _execution_payload(execution: Execution) -> dict[str, object]:
    return {
        "execution_id": str(execution.execution_id),
        "ownership": {
            "user_id": str(execution.ownership.user_id),
            "workspace_id": str(execution.ownership.workspace_id) if execution.ownership.workspace_id else None,
        },
        "agent_id": str(execution.agent_id),
        "task": {"task_ref": str(execution.task.task_ref), "revision": execution.task.revision},
        "state": execution.state.value,
        "state_version": int(execution.state_version),
        "correlation_id": str(execution.correlation_id),
        "causation_id": str(execution.causation_id) if execution.causation_id else None,
        "parent_execution_id": str(execution.parent_execution_id) if execution.parent_execution_id else None,
        "context_manifest_ref": str(execution.context_manifest_ref) if execution.context_manifest_ref else None,
        "result": {"result_ref": str(execution.result.result_ref)} if execution.result else None,
        "failure": (
            {"code": execution.failure.code.value, "detail_ref": str(execution.failure.detail_ref) if execution.failure.detail_ref else None}
            if execution.failure else None
        ),
        "cancellation_reason": (
            {"code": execution.cancellation_reason.code.value, "detail_ref": str(execution.cancellation_reason.detail_ref) if execution.cancellation_reason.detail_ref else None}
            if execution.cancellation_reason else None
        ),
        "limits": {
            "max_duration_seconds": execution.limits.max_duration_seconds,
            "max_iterations": execution.limits.max_iterations,
            "max_cost": str(execution.limits.max_cost) if execution.limits.max_cost is not None else None,
            "max_provider_tokens": execution.limits.max_provider_tokens,
        },
        "usage": {
            "duration_seconds": execution.usage.duration_seconds,
            "iterations": execution.usage.iterations,
            "provider_tokens": execution.usage.provider_tokens,
            "cost": str(execution.usage.cost),
        },
        "iteration_count": execution.iteration_count,
        "created_at": _timestamp(execution.created_at),
        "queued_at": _timestamp(execution.queued_at),
        "started_at": _timestamp(execution.started_at) if execution.started_at else None,
        "updated_at": _timestamp(execution.updated_at),
        "finished_at": _timestamp(execution.finished_at) if execution.finished_at else None,
        "checkpoint_ref": str(execution.checkpoint_ref) if execution.checkpoint_ref else None,
        "agent_config_version": execution.agent_config_version,
    }


def _execution_from_payload(payload: Mapping[str, object]) -> Execution:
    ownership = payload["ownership"]
    task = payload["task"]
    limits = payload["limits"]
    usage = payload["usage"]
    result = payload.get("result")
    failure = payload.get("failure")
    cancellation = payload.get("cancellation_reason")
    return Execution(
        execution_id=str(payload["execution_id"]),
        ownership=Ownership(str(ownership["user_id"]), ownership.get("workspace_id")),
        agent_id=str(payload["agent_id"]),
        task=TaskSnapshot(str(task["task_ref"]), int(task["revision"])),
        state=ExecutionState(str(payload["state"])),
        state_version=int(payload["state_version"]),
        correlation_id=str(payload["correlation_id"]),
        causation_id=payload.get("causation_id"),
        parent_execution_id=payload.get("parent_execution_id"),
        context_manifest_ref=payload.get("context_manifest_ref"),
        result=ExecutionResult(str(result["result_ref"])) if result else None,
        failure=ExecutionFailure(FailureReason(str(failure["code"])), failure.get("detail_ref")) if failure else None,
        cancellation_reason=(
            CancellationReason(CancellationReasonCode(str(cancellation["code"])), cancellation.get("detail_ref"))
            if cancellation else None
        ),
        limits=ExecutionLimits(
            max_duration_seconds=limits.get("max_duration_seconds"),
            max_iterations=limits.get("max_iterations"),
            max_cost=Decimal(str(limits["max_cost"])) if limits.get("max_cost") is not None else None,
            max_provider_tokens=limits.get("max_provider_tokens"),
        ),
        usage=ExecutionUsage(
            duration_seconds=int(usage["duration_seconds"]),
            iterations=int(usage["iterations"]),
            provider_tokens=int(usage["provider_tokens"]),
            cost=Decimal(str(usage["cost"])),
        ),
        iteration_count=int(payload["iteration_count"]),
        created_at=_read_timestamp(str(payload["created_at"])),
        queued_at=_read_timestamp(str(payload["queued_at"])),
        started_at=_read_timestamp(str(payload["started_at"])) if payload.get("started_at") else None,
        updated_at=_read_timestamp(str(payload["updated_at"])),
        finished_at=_read_timestamp(str(payload["finished_at"])) if payload.get("finished_at") else None,
        checkpoint_ref=payload.get("checkpoint_ref"),
        agent_config_version=payload.get("agent_config_version"),
    )


class ExecutionTransactionalPersistenceAdapter(LegacyPersistence):
    """Explicit bridge from the legacy Execution port to RFC 601 persistence."""

    def __init__(self, persistence: TransactionalPersistence, *, actor: str, purpose: str = "execution.persist") -> None:
        if not actor.strip() or not purpose.strip():
            raise ValueError("actor and purpose must be non-blank")
        self._persistence = persistence
        self._actor = actor
        self._purpose = purpose

    def _context(self, context: ExecutionCommandContext) -> PersistenceOperationContext:
        if context.purpose != self._purpose:
            raise UnauthorizedExecutionError(str(context.execution_id))
        return PersistenceOperationContext(
            user_id=str(context.user_id),
            workspace_id=str(context.workspace_id) if context.workspace_id is not None else None,
            agent_id=str(context.agent_id),
            execution_id=str(context.execution_id),
            correlation_id=str(context.correlation_id),
            purpose=self._purpose,
            actor=self._actor,
        )

    def seed(self, execution: Execution) -> None:
        context = PersistenceOperationContext(
            user_id=str(execution.ownership.user_id),
            workspace_id=str(execution.ownership.workspace_id) if execution.ownership.workspace_id else None,
            agent_id=str(execution.agent_id),
            execution_id=str(execution.execution_id),
            correlation_id=str(execution.correlation_id),
            purpose=self._purpose,
            actor=self._actor,
        )
        self._persistence.seed(  # type: ignore[attr-defined]
            AuthorizedRecord(
                record_ref=RecordReference(str(execution.execution_id)),
                record_type="execution",
                version=int(execution.state_version),
                context=context,
                classification=DataClassification.INTERNAL,
                data=_execution_payload(execution),
            )
        )

    def load(self, context: ExecutionCommandContext) -> Execution:
        return self.get(context.execution_id, context)

    def get(self, execution_id, context: ExecutionCommandContext | None = None) -> Execution:
        if context is None:
            raise UnauthorizedExecutionError(str(execution_id))
        canonical_context = self._context(context)
        result = self._persistence.read(
            AuthorizedRead(
                context=canonical_context,
                record_ref=RecordReference(str(execution_id)),
                record_type="execution",
                classification_ceiling=DataClassification.INTERNAL,
            )
        )
        from agentos.persistence import NotFound

        if isinstance(result, NotFound):
            raise ExecutionNotFoundError(str(execution_id))
        return _execution_from_payload(result.data)

    def lookup_idempotency(self, context: ExecutionCommandContext, idempotency_key: str) -> IdempotencyRecord | None:
        canonical_context = self._context(context)
        lookup = getattr(self._persistence, "_lookup_idempotency", None)
        if lookup is None:
            return None
        result = lookup(canonical_context, str(idempotency_key))
        if result is None:
            return None
        fingerprint, receipt, records = result
        if not records:
            return None
        return IdempotencyRecord(
            operation_fingerprint=fingerprint,
            receipt=self._legacy_receipt(receipt, context.execution_id, records[0].version),
            execution=_execution_from_payload(records[0].data),
        )

    def transact(self, request: LegacyTransactionRequest) -> LegacyTransactionResult:
        canonical_context = self._context(request.context)
        event = to_canonical_event(request.outbox.event, agent_id=canonical_context.agent_id)
        canonical_request = TransactionRequest(
            transaction_id=str(request.transaction_id),
            context=canonical_context,
            options=TransactionOptions(),
            idempotency_key=str(request.idempotency_key),
            fingerprint=request.operation_fingerprint,
            expected_versions=(
                (ExpectedVersion(RecordReference(str(request.change.execution_id)), int(request.change.expected_version)),)
                if request.change.expected_version is not None else ()
            ),
            changes=(
                RecordChange(
                    record_ref=RecordReference(str(request.change.execution_id)),
                    record_type="execution",
                    expected_version=(
                        int(request.change.expected_version) if request.change.expected_version is not None else None
                    ),
                    data=_execution_payload(request.change.new_execution),
                    classification=request.classification,
                ),
            ),
            audit=(
                __import__("agentos.persistence", fromlist=["AuditChange"]).AuditChange(
                    audit_ref=str(request.audit.audit_id),
                    record_ref=RecordReference(str(request.change.execution_id)),
                    decision=request.audit.decision,
                    resulting_version=int(request.audit.resulting_version),
                    fields={"from_state": request.audit.from_state, "to_state": request.audit.to_state},
                ),
            ),
            outbox=(
                OutboxChange(
                    event=event,
                    source_record_ref=RecordReference(str(request.outbox.source_execution_id)),
                    expected_source_version=int(request.outbox.expected_source_version),
                ),
            ),
        )
        result = self._persistence.transact(canonical_request)
        if isinstance(result, TransactionCommitted):
            resulting_version = result.records[0].version if result.records else 0
            receipt = self._legacy_receipt(result.receipt, request.change.execution_id, resulting_version)
            if result.already_applied:
                return LegacyTransactionCommitted(receipt, _execution_from_payload(result.records[0].data), True)
            return LegacyTransactionCommitted(receipt, _execution_from_payload(result.records[0].data), False)
        if isinstance(result, TransactionConflicted):
            return LegacyTransactionConflicted(result.conflicts[0].actual_version or 0)
        if isinstance(result, TransactionIndeterminate):
            return LegacyTransactionIndeterminate(result.transaction_id)
        if isinstance(result, TransactionRejected):
            return LegacyTransactionRejected(self._legacy_rejection(result.code))
        raise AssertionError("unknown canonical transaction result")

    def inspect_commit(self, *, context: ExecutionCommandContext, transaction_id, idempotency_key) -> LegacyTransactionReceipt:
        result = self._persistence.inspect_commit(
            InspectCommit(
                context=self._context(context),
                transaction_id=str(transaction_id),
                idempotency_key=str(idempotency_key),
            )
        )
        resulting_version = 0
        lookup = getattr(self._persistence, "_lookup_idempotency", None)
        if lookup is not None:
            inspected = lookup(self._context(context), str(idempotency_key))
            if inspected is not None and inspected[2]:
                resulting_version = int(inspected[2][0].version)
        return self._legacy_receipt(result, context.execution_id, resulting_version)

    @staticmethod
    def _legacy_receipt(receipt: TransactionReceipt, execution_id, resulting_version: int) -> LegacyTransactionReceipt:
        return LegacyTransactionReceipt(
            transaction_id=str(receipt.transaction_id),
            commit_state=TransactionCommitState(receipt.commit_state.value),
            affected_execution_id=execution_id,
            resulting_version=resulting_version,
            outbox_event_ids=tuple(item.value for item in receipt.outbox_refs),
        )

    @staticmethod
    def _legacy_rejection(code: PersistenceErrorCode) -> RejectionReason:
        if code is PersistenceErrorCode.IDEMPOTENCY_CONFLICT:
            return RejectionReason.IDEMPOTENCY_CONFLICT
        if code is PersistenceErrorCode.UNAUTHORIZED:
            return RejectionReason.UNAUTHORIZED
        return RejectionReason.PERSISTENCE_REJECTED


__all__ = ["ExecutionTransactionalPersistenceAdapter"]
