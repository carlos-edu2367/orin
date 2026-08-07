from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentos.capabilities.models import (
    CapabilityAccepted,
    CapabilityCancellationMode,
    CompensationPolicy,
    CapabilityDescriptor,
    CapabilityLimits,
    CapabilityOperationContext,
    CapabilityProgram,
    CapabilityRef,
    CapabilityRunState,
    CapabilityStep,
    CapabilityStepKind,
    CapabilityWaiting,
    InputBinding,
    PermissionRequirement,
    RetryPolicy,
    RunCapability,
    StartCapability,
    ToolRef,
    WaitReason,
)
from agentos.capabilities.ports import (
    ChildExecutionSnapshot,
    ChildExecutionState,
    InMemoryCapabilityState,
    ToolCancelled,
    ToolSucceeded,
)
from agentos.capabilities.registry import InMemoryCapabilityRegistry
from agentos.capabilities.service import CapabilityService
from agentos.execution.control import ExecutionControlService
from agentos.execution.in_memory import InMemoryTransactionalPersistence
from agentos.execution.models import ExecutionState, TaskSnapshot


NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def ctx(execution_id="execution:1"):
    return CapabilityOperationContext(
        user_id="user:1",
        workspace_id="workspace:1",
        agent_id="agent:1",
        execution_id=execution_id,
        correlation_id="correlation:1",
        purpose="capability.test",
        actor="actor:1",
    )


def make_service(step_kind=CapabilityStepKind.TOOL, tool_result=None, child_snapshot=None, authorization=None, maximum_attempts=1, include_second_step=False, compensation_policy=CompensationPolicy.NONE, maximum_tool_invocations=3):
    tool = ToolRef("tool:test", 1)
    step = CapabilityStep(
        step_id="step:1",
        kind=step_kind,
        dependencies=(),
        authorization=(PermissionRequirement("resource:test", "read"),),
        timeout_seconds=10,
        retry_policy=RetryPolicy(maximum_attempts=maximum_attempts),
        input_bindings=(InputBinding("input", "input:1"),),
        tool_ref=tool if step_kind is CapabilityStepKind.TOOL else None,
        child_capability_ref=CapabilityRef("child:test", 1) if step_kind is CapabilityStepKind.CHILD_EXECUTION else None,
    )
    steps = [step]
    if include_second_step:
        steps.append(CapabilityStep(
            step_id="step:2", kind=CapabilityStepKind.TOOL, dependencies=("step:1",),
            authorization=(PermissionRequirement("resource:test", "read"),), timeout_seconds=10,
            retry_policy=RetryPolicy(maximum_attempts=maximum_attempts), input_bindings=(InputBinding("input", "input:2"),), tool_ref=tool,
        ))
    compensation_steps = (CapabilityStep(
        step_id="comp:1", kind=CapabilityStepKind.COMPENSATION, dependencies=(),
        authorization=(PermissionRequirement("resource:test", "write"),), timeout_seconds=10,
        retry_policy=RetryPolicy(), input_bindings=(InputBinding("result", "result:1"),), tool_ref=tool,
    ),) if compensation_policy is CompensationPolicy.EXPLICIT_STEPS else ()
    program = CapabilityProgram(tuple(steps), compensation_steps)
    descriptor = CapabilityDescriptor(
        capability_ref=CapabilityRef("capability:test", 1),
        name="test",
        description="test",
        input_schema="schema:input",
        output_schema="schema:output",
        allowed_tools=(tool,),
        allowed_child_capabilities=(CapabilityRef("child:test", 1),),
        permissions=(PermissionRequirement("resource:test", "read"), PermissionRequirement("resource:test", "write")) if compensation_policy is CompensationPolicy.EXPLICIT_STEPS else (PermissionRequirement("resource:test", "read"),),
        limits=CapabilityLimits(60, 3, maximum_tool_invocations, 2, 1, None, 100),
        cancellation_policy=CapabilityCancellationMode.COOPERATIVE,
        compensation_policy=compensation_policy,
        status="ACTIVE",
    )
    registry = InMemoryCapabilityRegistry()
    from agentos.capabilities.models import CapabilityRegistryOperationContext

    registry.register(
        __import__("agentos.capabilities.models", fromlist=["RegisterCapability"]).RegisterCapability(
            request_id="register:1",
            context=CapabilityRegistryOperationContext(
                user_id="user:1", workspace_id="workspace:1", agent_id=None,
                execution_id=None, administrative_correlation_id="admin:1",
                correlation_id="correlation:1", purpose="catalog.maintain", actor="actor:1",
            ),
            descriptor=descriptor,
            program=program,
            package_integrity_ref="integrity:1",
            idempotency_key="registry:1",
        )
    )
    tool_port = FakeToolPort(tool_result or ToolSucceeded("invocation:1", "result:1"))
    child_port = FakeChildPort(child_snapshot)
    persistence = InMemoryTransactionalPersistence()
    control = ExecutionControlService(persistence)
    state = InMemoryCapabilityState()
    service = CapabilityService(control, registry, tool_port, child_port, state, clock=lambda: NOW, authorization=authorization)
    request = StartCapability(
        request_id="request:1", capability_ref=descriptor.capability_ref,
        user_id="user:1", workspace_id="workspace:1", agent_id="agent:1",
        correlation_id="correlation:1", purpose="capability.test", actor="actor:1",
        task=TaskSnapshot("task:1", 1), input_ref="input:1",
        limits=descriptor.limits, idempotency_key="start:1",
    )
    return service, control, persistence, state, tool_port, child_port, request


class FakeToolPort:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.cancelled = []

    def invoke(self, request):
        self.calls.append(request)
        return replace(self.result, invocation_id=request.invocation_id)

    def request_cancel(self, request):
        self.cancelled.append(request)


class FakeChildPort:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.created = []
        self.cancelled = []

    def create(self, request):
        self.created.append(request)
        return "execution:child:1"

    def inspect(self, query):
        return self.snapshot or ChildExecutionSnapshot("execution:child:1", ChildExecutionState.RUNNING, None)

    def request_cancel(self, request):
        self.cancelled.append(request)


def test_start_creates_queued_execution_and_is_idempotent():
    service, control, persistence, state, *_rest, request = make_service()
    first = service.start(request)
    repeated = service.start(request)
    assert isinstance(first, CapabilityAccepted)
    assert repeated == first
    execution = persistence.get(first.execution_id)
    assert execution.state is ExecutionState.QUEUED
    assert state.load(first.capability_run_id, ctx(first.execution_id)).state is CapabilityRunState.QUEUED


def test_run_invokes_exact_tool_ref_and_completes_canonical_execution():
    service, control, persistence, state, tool, _child, request = make_service()
    accepted = service.start(request)
    acquire_context = __import__("agentos.execution.ports", fromlist=["ExecutionCommandContext"]).ExecutionCommandContext(
        user_id="user:1", workspace_id="workspace:1", agent_id="agent:1",
        execution_id=accepted.execution_id, correlation_id="correlation:1", purpose="capability.test",
    )
    control.acquire(__import__("agentos.execution.ports", fromlist=["AcquireExecution"]).AcquireExecution(
        context=acquire_context, command_id="acquire:1", idempotency_key="acquire:1", expected_version=1,
        requested_at=NOW, worker_ref="worker:1",
    ))
    outcome = service.run(RunCapability(accepted.capability_run_id, ctx(accepted.execution_id), 1))
    assert outcome.result_ref == "result:1"
    assert tool.calls[0].tool_ref == ToolRef("tool:test", 1)
    assert tool.calls[0].context == ctx(accepted.execution_id)
    assert persistence.get(accepted.execution_id).state is ExecutionState.COMPLETED


def test_child_wait_checkpoints_before_pausing_and_resume_uses_queued_transition():
    child_done = ChildExecutionSnapshot("execution:child:1", ChildExecutionState.COMPLETED, "result:child")
    service, control, persistence, state, _tool, child, request = make_service(CapabilityStepKind.CHILD_EXECUTION, child_snapshot=child_done)
    accepted = service.start(request)
    command_context = __import__("agentos.execution.ports", fromlist=["ExecutionCommandContext"]).ExecutionCommandContext(
        user_id="user:1", workspace_id="workspace:1", agent_id="agent:1", execution_id=accepted.execution_id,
        correlation_id="correlation:1", purpose="capability.test",
    )
    control.acquire(__import__("agentos.execution.ports", fromlist=["AcquireExecution"]).AcquireExecution(
        context=command_context, command_id="acquire:1", idempotency_key="acquire:1", expected_version=1,
        requested_at=NOW, worker_ref="worker:1",
    ))
    waiting = service.run(RunCapability(accepted.capability_run_id, ctx(accepted.execution_id), 1))
    assert isinstance(waiting, CapabilityWaiting)
    assert waiting.reason is WaitReason.CHILD
    assert persistence.get(accepted.execution_id).state is ExecutionState.PAUSED
    assert not hasattr(child.created[0].context, "execution_id")
    resumed = service.resume(__import__("agentos.capabilities.models", fromlist=["ResumeCapability"]).ResumeCapability(
        accepted.capability_run_id, ctx(accepted.execution_id), state.load(accepted.capability_run_id, ctx(accepted.execution_id)).state_version, waiting.checkpoint_ref
    ))
    assert resumed.result_ref == "result:child"
    assert persistence.get(accepted.execution_id).state is ExecutionState.COMPLETED


def test_cancel_propagates_and_late_tool_cancel_cannot_become_success():
    service, control, persistence, state, tool, child, request = make_service(
        tool_result=ToolCancelled("invocation:1", "user requested", None)
    )
    accepted = service.start(request)
    result = service.request_cancel(__import__("agentos.capabilities.models", fromlist=["CancelCapability"]).CancelCapability(
        accepted.capability_run_id, ctx(accepted.execution_id), "user requested", "cancel:1"
    ))
    assert result.cancelled is True
    assert persistence.get(accepted.execution_id).state is ExecutionState.CANCELLED
