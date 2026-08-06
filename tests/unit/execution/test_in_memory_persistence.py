from __future__ import annotations

from agentos.execution.models import ExecutionState
from agentos.execution.ports import Rejected, TransactionCommitState


def test_state_audit_and_outbox_are_not_changed_when_transaction_is_rejected(make_control, command_context, now):
    from agentos.execution.ports import TransitionExecution

    control, persistence = make_control(ExecutionState.QUEUED)
    persistence.reject_next("persistence-rejected")
    command = TransitionExecution(
        context=command_context,
        command_id="command-1",
        idempotency_key="key-1",
        expected_version=1,
        requested_at=now,
        target_state=ExecutionState.STARTING,
        reason_code="dispatch",
    )

    result = control.transition(command)

    assert isinstance(result, Rejected)
    assert persistence.get("execution-1").state is ExecutionState.QUEUED
    assert persistence.audit_log == []
    assert persistence.outbox == []


def test_indeterminate_commit_can_be_inspected_and_contains_state_and_outbox(
    make_control, command_context, now
):
    from agentos.execution.ports import TransitionExecution

    control, persistence = make_control(ExecutionState.QUEUED)
    persistence.indeterminate_next_commit()
    command = TransitionExecution(
        context=command_context,
        command_id="command-unknown",
        idempotency_key="unknown-key",
        expected_version=1,
        requested_at=now,
        target_state=ExecutionState.STARTING,
        reason_code="dispatch",
    )

    result = control.transition(command)

    assert result.transaction_id
    receipt = persistence.inspect_commit(
        context=command_context,
        transaction_id=result.transaction_id,
        idempotency_key=command.idempotency_key,
    )
    assert receipt.commit_state is TransactionCommitState.COMMITTED
    assert persistence.get("execution-1").state is ExecutionState.STARTING
    assert len(persistence.audit_log) == 1
    assert len(persistence.outbox) == 1
