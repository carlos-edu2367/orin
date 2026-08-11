from __future__ import annotations

from dataclasses import replace

import pytest

from agentos.execution.events import ExecutionEventType
from agentos.execution.models import (
    CancellationReason,
    CancellationReasonCode,
    ExecutionFailure,
    FailureReason,
    ExecutionState,
)
from agentos.execution.ports import (
    AcquireExecution,
    AlreadyApplied,
    CancelExecution,
    Conflict,
    CommitExecutionChanges,
    CreateExecution,
    ExecutionRelatedChange,
    ProvideExecutionInput,
    PauseExecution,
    Rejected,
    RejectionReason,
    ResumeExecution,
    TransitionExecution,
)


VALID_TRANSITIONS = (
    (ExecutionState.QUEUED, ExecutionState.STARTING),
    (ExecutionState.QUEUED, ExecutionState.CANCELLED),
    (ExecutionState.STARTING, ExecutionState.RUNNING),
    (ExecutionState.STARTING, ExecutionState.QUEUED),
    (ExecutionState.STARTING, ExecutionState.FAILED),
    (ExecutionState.STARTING, ExecutionState.CANCELLED),
    (ExecutionState.RUNNING, ExecutionState.WAITING_TOOL),
    (ExecutionState.RUNNING, ExecutionState.WAITING_USER),
    (ExecutionState.RUNNING, ExecutionState.PAUSED),
    (ExecutionState.RUNNING, ExecutionState.QUEUED),
    (ExecutionState.RUNNING, ExecutionState.COMPLETED),
    (ExecutionState.RUNNING, ExecutionState.FAILED),
    (ExecutionState.RUNNING, ExecutionState.CANCELLED),
    (ExecutionState.WAITING_TOOL, ExecutionState.RUNNING),
    (ExecutionState.WAITING_TOOL, ExecutionState.PAUSED),
    (ExecutionState.WAITING_TOOL, ExecutionState.QUEUED),
    (ExecutionState.WAITING_TOOL, ExecutionState.FAILED),
    (ExecutionState.WAITING_TOOL, ExecutionState.CANCELLED),
    (ExecutionState.WAITING_USER, ExecutionState.QUEUED),
    (ExecutionState.WAITING_USER, ExecutionState.PAUSED),
    (ExecutionState.WAITING_USER, ExecutionState.FAILED),
    (ExecutionState.WAITING_USER, ExecutionState.CANCELLED),
    (ExecutionState.PAUSED, ExecutionState.QUEUED),
    (ExecutionState.PAUSED, ExecutionState.FAILED),
    (ExecutionState.PAUSED, ExecutionState.CANCELLED),
)


def transition_command(command_context, now, target, version=1, *, key="key-1"):
    return TransitionExecution(
        context=command_context,
        command_id=f"command-{key}",
        idempotency_key=key,
        expected_version=version,
        requested_at=now,
        target_state=target,
        reason_code="test-transition",
        result_ref="result-1" if target is ExecutionState.COMPLETED else None,
        failure=ExecutionFailure(code=FailureReason.RUNTIME_ERROR, detail_ref="failure-1")
        if target is ExecutionState.FAILED
        else None,
        cancellation_reason=CancellationReason(code=CancellationReasonCode.USER_REQUESTED)
        if target is ExecutionState.CANCELLED
        else None,
    )


@pytest.mark.parametrize("source,target", VALID_TRANSITIONS)
def test_accepts_every_rfc_102_transition(make_control, command_context, now, source, target):
    control, persistence = make_control(source)

    result = control.transition(transition_command(command_context, now, target))

    assert result.resulting_version == 2
    current = persistence.get("execution-1")
    assert current.state is target
    assert current.state_version == 2
    assert len(persistence.audit_log) == 1
    assert len(persistence.outbox) == 1
    expected_event_type = {
        ExecutionState.QUEUED: ExecutionEventType.EXECUTION_QUEUED,
        ExecutionState.STARTING: ExecutionEventType.EXECUTION_STARTED,
        ExecutionState.RUNNING: ExecutionEventType.EXECUTION_STARTED,
        ExecutionState.WAITING_TOOL: ExecutionEventType.EXECUTION_WAITING_FOR_TOOL,
        ExecutionState.WAITING_USER: ExecutionEventType.EXECUTION_WAITING_FOR_USER,
        ExecutionState.PAUSED: ExecutionEventType.EXECUTION_PAUSED,
        ExecutionState.COMPLETED: ExecutionEventType.EXECUTION_FINISHED,
        ExecutionState.FAILED: ExecutionEventType.EXECUTION_FAILED,
        ExecutionState.CANCELLED: ExecutionEventType.EXECUTION_CANCELLED,
    }[target]
    if source is ExecutionState.PAUSED and target is ExecutionState.QUEUED:
        expected_event_type = ExecutionEventType.EXECUTION_RESUMED
    if source is ExecutionState.QUEUED and target is ExecutionState.STARTING:
        expected_event_type = ExecutionEventType.EXECUTION_QUEUED
    assert persistence.outbox[0].event.event_type is expected_event_type


@pytest.mark.parametrize(
    "source,target",
    [
        (ExecutionState.QUEUED, ExecutionState.RUNNING),
        (ExecutionState.QUEUED, ExecutionState.COMPLETED),
        (ExecutionState.WAITING_TOOL, ExecutionState.COMPLETED),
        (ExecutionState.WAITING_USER, ExecutionState.RUNNING),
        (ExecutionState.PAUSED, ExecutionState.RUNNING),
    ],
)
def test_rejects_transition_missing_from_rfc_102(make_control, command_context, now, source, target):
    control, persistence = make_control(source)

    result = control.transition(transition_command(command_context, now, target))

    assert isinstance(result, Rejected)
    assert result.current_state is source
    assert persistence.get("execution-1").state is source
    assert persistence.audit_log == []
    assert persistence.outbox == []


@pytest.mark.parametrize("terminal", [ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED])
def test_terminal_execution_is_immutable(make_control, command_context, now, terminal):
    control, persistence = make_control(terminal)

    result = control.transition(transition_command(command_context, now, ExecutionState.QUEUED))

    assert isinstance(result, Rejected)
    assert result.current_state is terminal
    assert persistence.get("execution-1").state is terminal
    assert persistence.audit_log == []
    assert persistence.outbox == []


def test_rejects_stale_expected_version(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.QUEUED, version=3)

    result = control.transition(transition_command(command_context, now, ExecutionState.STARTING, version=2))

    assert isinstance(result, Conflict)
    assert result.current_version == 3
    assert persistence.get("execution-1").state is ExecutionState.QUEUED
    assert persistence.outbox == []


def test_repeated_command_is_idempotent_without_duplicate_outbox(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.QUEUED)
    command = transition_command(command_context, now, ExecutionState.STARTING)

    first = control.transition(command)
    repeated = control.transition(command)

    assert first.resulting_version == 2
    assert isinstance(repeated, AlreadyApplied)
    assert repeated.resulting_version == 2
    assert persistence.get("execution-1").state_version == 2
    assert len(persistence.audit_log) == 1
    assert len(persistence.outbox) == 1


def test_cancel_command_records_reason_and_terminal_event(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.RUNNING)
    command = CancelExecution(
        context=command_context,
        command_id="cancel-1",
        idempotency_key="cancel-key-1",
        expected_version=1,
        requested_at=now,
        reason=CancellationReason(code=CancellationReasonCode.USER_REQUESTED),
    )

    result = control.request_cancel(command)

    assert result.resulting_version == 2
    current = persistence.get("execution-1")
    assert current.state is ExecutionState.CANCELLED
    assert current.cancellation_reason.code is CancellationReasonCode.USER_REQUESTED
    assert persistence.outbox[0].event.event_type is ExecutionEventType.EXECUTION_CANCELLED


def test_acquire_command_moves_queued_execution_to_starting(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.QUEUED)
    command = AcquireExecution(
        context=command_context,
        command_id="acquire-1",
        idempotency_key="acquire-key-1",
        expected_version=1,
        requested_at=now,
        worker_ref="worker-1",
    )

    result = control.acquire(command)

    assert result.resulting_version == 2
    assert persistence.get("execution-1").state is ExecutionState.STARTING


def test_provide_input_moves_waiting_user_execution_to_queued(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.WAITING_USER)
    command = ProvideExecutionInput(
        context=command_context,
        command_id="input-1",
        idempotency_key="input-key-1",
        expected_version=1,
        requested_at=now,
        input_ref="input-1",
    )

    result = control.provide_input(command)

    assert result.resulting_version == 2
    assert persistence.get("execution-1").state is ExecutionState.QUEUED


def test_failure_command_records_sanitized_failure(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.STARTING)
    command = transition_command(command_context, now, ExecutionState.FAILED)

    result = control.transition(command)

    assert result.resulting_version == 2
    current = persistence.get("execution-1")
    assert current.state is ExecutionState.FAILED
    assert current.failure.code is FailureReason.RUNTIME_ERROR
    assert current.failure.detail_ref == "failure-1"
def test_pause_and_resume_use_their_specialized_commands(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.RUNNING)
    pause = PauseExecution(
        context=command_context,
        command_id="pause-1",
        idempotency_key="pause-key-1",
        expected_version=1,
        requested_at=now,
    )
    paused = control.request_pause(pause)
    assert paused.resulting_version == 2

    resume_context = replace(command_context)
    resume = ResumeExecution(
        context=resume_context,
        command_id="resume-1",
        idempotency_key="resume-key-1",
        expected_version=2,
        requested_at=now,
    )
    resumed = control.request_resume(resume)

    assert resumed.resulting_version == 3
    assert persistence.get("execution-1").state is ExecutionState.QUEUED


def test_commit_prepares_related_change_and_outbox_together(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.RUNNING)
    command = CommitExecutionChanges(
        context=command_context,
        command_id="commit-1",
        idempotency_key="commit-key-1",
        expected_version=1,
        requested_at=now,
        expected_state=ExecutionState.RUNNING,
        target_state=ExecutionState.COMPLETED,
        reason_code="result-confirmed",
        result_ref="result-1",
        changes=(ExecutionRelatedChange(kind="usage-recorded", reference="usage-1"),),
    )

    result = control.commit(command)

    assert result.resulting_version == 2
    assert persistence.get("execution-1").state is ExecutionState.COMPLETED
    assert len(persistence.audit_log) == 1
    assert len(persistence.outbox) == 1


def test_current_signal_reads_the_authorized_execution(make_control, command_context):
    from agentos.execution.ports import ControlSignal, ExecutionControlQuery

    control, _ = make_control(ExecutionState.RUNNING)

    signal = control.current_signal(ExecutionControlQuery(context=command_context))

    assert signal is ControlSignal.CONTINUE


def test_missing_expected_version_is_rejected(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.QUEUED)
    command = transition_command(command_context, now, ExecutionState.STARTING, version=None)

    result = control.transition(command)

    assert isinstance(result, Rejected)
    assert persistence.get("execution-1").state is ExecutionState.QUEUED


def test_same_idempotency_key_with_different_command_is_rejected(make_control, command_context, now):
    control, persistence = make_control(ExecutionState.QUEUED)
    first = transition_command(command_context, now, ExecutionState.STARTING, key="same-key")
    incompatible = transition_command(command_context, now, ExecutionState.CANCELLED, key="same-key")

    control.transition(first)
    result = control.transition(incompatible)

    assert isinstance(result, Rejected)
    assert persistence.get("execution-1").state is ExecutionState.STARTING
    assert len(persistence.outbox) == 1


def test_same_idempotency_key_is_scoped_to_execution_ownership(make_control, command_context, now):
    from dataclasses import replace
    from agentos.execution.in_memory import InMemoryTransactionalPersistence

    persistence = InMemoryTransactionalPersistence()
    first = make_control(ExecutionState.QUEUED)[1].get("execution-1")
    second = replace(
        first,
        execution_id="execution-2",
        ownership=replace(first.ownership, workspace_id="workspace-2"),
        correlation_id="correlation-2",
    )
    persistence.seed(first)
    persistence.seed(second)
    control = __import__("agentos.execution.control", fromlist=["ExecutionControlService"]).ExecutionControlService(persistence)
    first_context = command_context
    second_context = replace(
        command_context,
        workspace_id="workspace-2",
        execution_id="execution-2",
        correlation_id="correlation-2",
    )

    first_result = control.transition(transition_command(first_context, now, ExecutionState.STARTING, key="shared-key"))
    second_result = control.transition(transition_command(second_context, now, ExecutionState.STARTING, key="shared-key"))

    assert first_result.resulting_version == 2
    assert second_result.resulting_version == 2
    assert persistence.get("execution-2").state is ExecutionState.STARTING


def test_legacy_execution_event_rejects_sensitive_or_unbounded_payload(now):
    from agentos.execution.events import DataClassification, EventEnvelope
    from agentos.execution.models import Ownership

    with pytest.raises(ValueError):
        EventEnvelope(
            event_id="event:unsafe",
            event_type=ExecutionEventType.EXECUTION_STARTED,
            event_version=1,
            occurred_at=now,
            source="execution-control",
            correlation_id="correlation-1",
            causation_id=None,
            sequence=1,
            ownership=Ownership("user-1", "workspace-1"),
            execution_id="execution-1",
            classification=DataClassification.INTERNAL,
            payload={"prompt": "do not include"},
        )


def test_commit_applies_reference_only_usage_and_checkpoint_changes(
    make_control, command_context, now
):
    control, persistence = make_control(ExecutionState.RUNNING)
    command = CommitExecutionChanges(
        context=command_context,
        command_id="commit-accounting-1",
        idempotency_key="commit-accounting-key-1",
        expected_version=1,
        requested_at=now,
        expected_state=ExecutionState.RUNNING,
        target_state=ExecutionState.WAITING_TOOL,
        reason_code="provider-accounted",
        changes=(
            ExecutionRelatedChange(kind="usage-recorded", duration_seconds=2, iterations=1),
            ExecutionRelatedChange(kind="provider-tokens-recorded", reference="tokens:confirmed"),
            ExecutionRelatedChange(kind="checkpoint-recorded", reference="checkpoint:1"),
        ),
    )

    result = control.commit(command)

    assert result.resulting_version == 2
    current = persistence.get("execution-1")
    assert current.state is ExecutionState.WAITING_TOOL
    assert current.usage.duration_seconds == 2
    assert current.usage.iterations == 1
    assert current.checkpoint_ref == "checkpoint:1"


def test_load_reads_authorized_execution(make_control, command_context):
    control, _ = make_control(ExecutionState.RUNNING)

    current = control.load(command_context)

    assert current.execution_id == command_context.execution_id
    assert current.agent_id == command_context.agent_id


def test_command_context_rejects_blank_purpose():
    from agentos.execution.ports import ExecutionCommandContext

    with pytest.raises(ValueError):
        ExecutionCommandContext(
            user_id="user-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            execution_id="execution-1",
            correlation_id="correlation-1",
            purpose="",
        )


def test_cross_ownership_command_is_rejected_without_leaking_storage_error(
    make_control, command_context, now
):
    control, persistence = make_control(ExecutionState.QUEUED)
    foreign_context = replace(command_context, user_id="other-user")

    result = control.transition(transition_command(foreign_context, now, ExecutionState.STARTING))

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.UNAUTHORIZED
    assert persistence.get("execution-1").state is ExecutionState.QUEUED


def test_specialized_command_without_command_id_is_rejected(command_context, now):
    with pytest.raises(ValueError):
        PauseExecution(
            context=command_context,
            command_id="",
            idempotency_key="pause-key-1",
            expected_version=1,
            requested_at=now,
        )


def test_create_command_persists_queued_execution_and_outbox(command_context, now):
    from agentos.execution.models import Execution, ExecutionLimits, ExecutionUsage, Ownership, TaskSnapshot
    from agentos.execution.in_memory import InMemoryTransactionalPersistence
    from agentos.execution.control import ExecutionControlService

    persistence = InMemoryTransactionalPersistence()
    control = ExecutionControlService(persistence)
    execution = Execution(
        execution_id="execution-1",
        ownership=Ownership(user_id="user-1", workspace_id="workspace-1"),
        agent_id="agent-1",
        task=TaskSnapshot(task_ref="task-1", revision=1),
        state=ExecutionState.QUEUED,
        state_version=1,
        correlation_id="correlation-1",
        causation_id=None,
        parent_execution_id=None,
        context_manifest_ref=None,
        result=None,
        failure=None,
        cancellation_reason=None,
        limits=ExecutionLimits(max_duration_seconds=60, max_iterations=10),
        usage=ExecutionUsage(),
        iteration_count=0,
        created_at=now,
        queued_at=now,
        started_at=None,
        updated_at=now,
        finished_at=None,
    )
    command = CreateExecution(
        context=command_context,
        command_id="create-1",
        idempotency_key="create-key-1",
        expected_version=None,
        requested_at=now,
        execution=execution,
    )

    result = control.create(command)

    assert result.resulting_version == 1
    assert persistence.get("execution-1").state is ExecutionState.QUEUED
    assert persistence.outbox[0].event.event_type is ExecutionEventType.EXECUTION_QUEUED
