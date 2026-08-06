from datetime import UTC, datetime

from agentos.events.models import DataClassification
from agentos.persistence import AuthorizedRead, InMemoryTransactionalPersistence, PersistenceOperationContext, RecordReference
from agentos.persistence.execution_compat import ExecutionTransactionalPersistenceAdapter
from agentos.execution.control import ExecutionControlService
from agentos.execution.models import Execution, ExecutionLimits, ExecutionState, ExecutionUsage, Ownership, TaskSnapshot
from agentos.execution.ports import AlreadyApplied, ExecutionCommandContext, Indeterminate, TransactionCommitState, TransitionExecution


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def old_context() -> ExecutionCommandContext:
    return ExecutionCommandContext(
        user_id="user-1", workspace_id="workspace-1", agent_id="agent-1",
        execution_id="execution-1", correlation_id="correlation-1", purpose="execution.persist",
    )


def execution() -> Execution:
    return Execution(
        execution_id="execution-1", ownership=Ownership("user-1", "workspace-1"), agent_id="agent-1",
        task=TaskSnapshot("task-1", 1), state=ExecutionState.QUEUED, state_version=1,
        correlation_id="correlation-1", causation_id=None, parent_execution_id=None,
        context_manifest_ref=None, result=None, failure=None, cancellation_reason=None,
        limits=ExecutionLimits(max_duration_seconds=60, max_iterations=10), usage=ExecutionUsage(),
        iteration_count=0, created_at=NOW, queued_at=NOW, started_at=None, updated_at=NOW, finished_at=None,
    )


def test_execution_control_uses_explicit_canonical_persistence_bridge():
    canonical = InMemoryTransactionalPersistence()
    adapter = ExecutionTransactionalPersistenceAdapter(canonical, actor="actor:execution-control")
    adapter.seed(execution())
    control = ExecutionControlService(adapter)
    ctx = old_context()

    result = control.transition(TransitionExecution(
        context=ctx, command_id="command:1", idempotency_key="transition:1", expected_version=1,
        requested_at=NOW, target_state=ExecutionState.STARTING, reason_code="dispatch",
    ))

    assert result.resulting_version == 2
    record = canonical.read(AuthorizedRead(
        context=PersistenceOperationContext(
            user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1",
            correlation_id="correlation-1", purpose="execution.persist", actor="actor:execution-control",
        ),
        record_ref=RecordReference("execution-1"), record_type="execution",
        classification_ceiling=DataClassification.INTERNAL,
    ))
    assert record.version == 2
    assert len(canonical.audit_records) == 1
    assert len(canonical.confirmed_outbox()) == 1


def test_indeterminate_execution_commit_requires_inspection():
    canonical = InMemoryTransactionalPersistence()
    adapter = ExecutionTransactionalPersistenceAdapter(canonical, actor="actor:execution-control")
    adapter.seed(execution())
    canonical.indeterminate_next()
    control = ExecutionControlService(adapter)
    ctx = old_context()

    result = control.transition(TransitionExecution(
        context=ctx, command_id="command:unknown", idempotency_key="transition:unknown", expected_version=1,
        requested_at=NOW, target_state=ExecutionState.STARTING, reason_code="dispatch",
    ))

    assert isinstance(result, Indeterminate)
    receipt = adapter.inspect_commit(context=ctx, transaction_id=result.transaction_id, idempotency_key="transition:unknown")
    assert receipt.commit_state is TransactionCommitState.COMMITTED


def test_compatibility_bridge_preserves_execution_idempotency_after_repeat():
    canonical = InMemoryTransactionalPersistence()
    adapter = ExecutionTransactionalPersistenceAdapter(canonical, actor="actor:execution-control")
    adapter.seed(execution())
    control = ExecutionControlService(adapter)
    command = TransitionExecution(
        context=old_context(), command_id="command:repeat", idempotency_key="transition:repeat", expected_version=1,
        requested_at=NOW, target_state=ExecutionState.STARTING, reason_code="dispatch",
    )

    control.transition(command)
    repeated = control.transition(command)

    assert isinstance(repeated, AlreadyApplied)
