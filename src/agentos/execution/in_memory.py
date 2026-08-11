from __future__ import annotations

from dataclasses import dataclass

from .events import AuditRecord, OutboxEntry
from .models import Execution, ExecutionId, IdempotencyKey, TransactionId
from .ports import (
    ExecutionCommandContext,
    ExecutionNotFoundError,
    IdempotencyRecord,
    RejectionReason,
    TransactionCommitState,
    TransactionCommitted,
    TransactionConflicted,
    TransactionIndeterminate,
    TransactionReceipt,
    TransactionRejected,
    TransactionRequest,
    TransactionResult,
    TransactionalPersistence,
    UnauthorizedExecutionError,
)


@dataclass(frozen=True, slots=True)
class _StoredIdempotency:
    operation_fingerprint: str
    receipt: TransactionReceipt
    execution: Execution


class InMemoryTransactionalPersistence(TransactionalPersistence):
    """Test-only adapter that commits execution, audit, and outbox atomically."""

    def __init__(self) -> None:
        self._executions: dict[str, Execution] = {}
        self._idempotency: dict[tuple[str, str, str], _StoredIdempotency] = {}
        self._receipts: dict[str, TransactionReceipt] = {}
        self.audit_log: list[AuditRecord] = []
        self.outbox: list[OutboxEntry] = []
        self._rejection: str | None = None
        self._indeterminate = False

    def seed(self, execution: Execution) -> None:
        if execution.execution_id in self._executions:
            raise ValueError("execution already seeded")
        self._executions[execution.execution_id] = execution

    def confirmed_outbox(self) -> tuple[OutboxEntry, ...]:
        """Return the committed outbox view for read-only publisher adapters."""
        return tuple(self.outbox)

    def get(self, execution_id: ExecutionId, context: ExecutionCommandContext | None = None) -> Execution:
        execution = self._executions.get(str(execution_id))
        if execution is None:
            raise ExecutionNotFoundError(str(execution_id))
        if context is not None and (
            str(context.user_id) != str(execution.ownership.user_id)
            or context.workspace_id != execution.ownership.workspace_id
            or str(context.agent_id) != str(execution.agent_id)
            or str(context.correlation_id) != str(execution.correlation_id)
        ):
            raise UnauthorizedExecutionError(str(execution_id))
        return execution

    def lookup_idempotency(
        self, context: ExecutionCommandContext, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None:
        record = self._idempotency.get(self._key(context, idempotency_key))
        if record is None:
            return None
        return IdempotencyRecord(
            operation_fingerprint=record.operation_fingerprint,
            receipt=record.receipt,
            execution=record.execution,
        )

    def transact(self, request: TransactionRequest) -> TransactionResult:
        idempotency_key = self._key(request.context, request.idempotency_key)
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            if existing.operation_fingerprint != request.operation_fingerprint:
                return TransactionRejected(RejectionReason.IDEMPOTENCY_CONFLICT)
            return TransactionCommitted(existing.receipt, existing.execution, already_applied=True)

        if self._rejection is not None:
            self._rejection = None
            return TransactionRejected(RejectionReason.PERSISTENCE_REJECTED)

        current = self._executions.get(str(request.change.execution_id))
        new_execution = request.change.new_execution
        if (
            new_execution.execution_id != request.context.execution_id
            or new_execution.ownership.user_id != request.context.user_id
            or new_execution.ownership.workspace_id != request.context.workspace_id
            or new_execution.agent_id != request.context.agent_id
            or new_execution.correlation_id != request.context.correlation_id
            or request.outbox.event.execution_id != new_execution.execution_id
            or request.outbox.event.ownership != new_execution.ownership
            or request.outbox.event.correlation_id != new_execution.correlation_id
            or request.outbox.event.sequence != new_execution.state_version
            or request.outbox.source_execution_id != new_execution.execution_id
            or request.outbox.expected_source_version != new_execution.state_version
            or request.audit.user_id != request.context.user_id
            or request.audit.workspace_id != request.context.workspace_id
            or request.audit.agent_id != request.context.agent_id
            or request.audit.execution_id != request.context.execution_id
            or request.audit.correlation_id != request.context.correlation_id
            or request.audit.purpose != request.context.purpose
            or request.audit.command_id != request.outbox.event.causation_id
            or request.audit.to_state != new_execution.state.value
            or request.audit.resulting_version != new_execution.state_version
        ):
            return TransactionRejected(RejectionReason.UNAUTHORIZED)
        if current is not None and (
            current.ownership.user_id != request.context.user_id
            or current.ownership.workspace_id != request.context.workspace_id
            or current.agent_id != request.context.agent_id
            or current.correlation_id != request.context.correlation_id
        ):
            return TransactionRejected(RejectionReason.UNAUTHORIZED)
        expected_version = request.change.expected_version
        if expected_version is None:
            if current is not None:
                return TransactionConflicted(current.state_version)
            if new_execution.state_version != 1:
                return TransactionRejected(RejectionReason.PERSISTENCE_REJECTED)
        elif current is None or current.state_version != expected_version:
            return TransactionConflicted(current.state_version if current is not None else 0)
        elif new_execution.state_version != current.state_version + 1:
            return TransactionRejected(RejectionReason.PERSISTENCE_REJECTED)

        receipt = TransactionReceipt(
            transaction_id=request.transaction_id,
            commit_state=TransactionCommitState.COMMITTED,
            affected_execution_id=new_execution.execution_id,
            resulting_version=new_execution.state_version,
            outbox_event_ids=(request.outbox.event.event_id,),
        )

        # These three writes are one in-memory commit unit: no mutation occurs before all checks succeed.
        self._executions[str(new_execution.execution_id)] = new_execution
        self.audit_log.append(request.audit)
        self.outbox.append(request.outbox)
        self._receipts[str(request.transaction_id)] = receipt
        self._idempotency[idempotency_key] = _StoredIdempotency(
            operation_fingerprint=request.operation_fingerprint,
            receipt=receipt,
            execution=new_execution,
        )

        if self._indeterminate:
            self._indeterminate = False
            return TransactionIndeterminate(request.transaction_id)
        return TransactionCommitted(receipt, new_execution)

    def inspect_commit(
        self,
        *,
        context: ExecutionCommandContext,
        transaction_id: TransactionId,
        idempotency_key: IdempotencyKey,
    ) -> TransactionReceipt:
        record = self._idempotency.get(self._key(context, idempotency_key))
        if record is None or record.receipt.transaction_id != transaction_id:
            raise LookupError(str(transaction_id))
        return record.receipt

    def reject_next(self, reason: str) -> None:
        self._rejection = reason

    def indeterminate_next_commit(self) -> None:
        self._indeterminate = True

    @staticmethod
    def _key(context: ExecutionCommandContext, idempotency_key: IdempotencyKey) -> tuple[str, ...]:
        return (
            str(context.user_id),
            str(context.workspace_id),
            str(context.agent_id),
            str(context.execution_id),
            str(context.correlation_id),
            str(context.purpose),
            str(idempotency_key),
        )
