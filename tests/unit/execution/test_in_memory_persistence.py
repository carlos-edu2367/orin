from __future__ import annotations

from dataclasses import replace

from agentos.execution.models import ExecutionState
from agentos.execution.ports import (
    Rejected,
    TransactionCommitState,
    TransactionRejected,
)


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


def _capture_transition_request(make_control, command_context, now):
    from agentos.execution.control import ExecutionControlService
    from agentos.execution.in_memory import InMemoryTransactionalPersistence
    from agentos.execution.ports import TransitionExecution

    persistence = InMemoryTransactionalPersistence()

    class Capture:
        def __init__(self):
            self.request = None

        def get(self, execution_id, context=None):
            return persistence.get(execution_id, context)

        def lookup_idempotency(self, context, idempotency_key):
            return persistence.lookup_idempotency(context, idempotency_key)

        def transact(self, request):
            self.request = request
            return TransactionRejected("PERSISTENCE_REJECTED")

    capture = Capture()
    execution = make_control(ExecutionState.QUEUED)[1].get("execution-1")
    persistence.seed(execution)
    control = ExecutionControlService(capture)
    control.transition(
        TransitionExecution(
            context=command_context,
            command_id="capture-command",
            idempotency_key="capture-key",
            expected_version=1,
            requested_at=now,
            target_state=ExecutionState.STARTING,
            reason_code="dispatch",
        )
    )
    assert capture.request is not None
    return persistence, capture.request


def test_transaction_rejects_audit_scope_that_does_not_match_change(
    make_control, command_context, now
):
    persistence, request = _capture_transition_request(make_control, command_context, now)

    forged = replace(request, audit=replace(request.audit, user_id="other-user"))
    result = persistence.transact(forged)

    assert isinstance(result, TransactionRejected)
    assert result.reason == "UNAUTHORIZED"
    assert persistence.audit_log == []
    assert persistence.outbox == []


def test_transaction_rejects_a_state_version_jump(
    make_control, command_context, now
):
    persistence, request = _capture_transition_request(make_control, command_context, now)
    jumped = replace(
        request,
        change=replace(
            request.change,
            new_execution=replace(request.change.new_execution, state_version=9),
        ),
        outbox=replace(
            request.outbox,
            event=replace(request.outbox.event, sequence=9),
            expected_source_version=9,
        ),
        audit=replace(request.audit, resulting_version=9),
    )

    result = persistence.transact(jumped)

    assert isinstance(result, TransactionRejected)
    assert result.reason == "PERSISTENCE_REJECTED"
    assert persistence.audit_log == []
    assert persistence.outbox == []
