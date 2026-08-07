from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentos.execution.models import (
    CancellationReason,
    CancellationReasonCode,
    Execution,
    ExecutionFailure,
    ExecutionLimits,
    ExecutionState,
    Ownership,
    TaskSnapshot,
)
from agentos.execution.ports import (
    Accepted,
    AcquireExecution,
    CancelExecution,
    CommitExecutionChanges,
    ExecutionCommandContext,
    ExecutionControl,
    ExecutionRelatedChange,
    ExecutionControlQuery,
    ControlSignal,
    Rejected,
    TransitionExecution,
)

from .models import (
    CapabilityAccepted,
    CapabilityCancelled,
    CapabilityCheckpoint,
    CapabilityDescriptor,
    CapabilityEventType,
    CapabilityFailed,
    CapabilityOperationContext,
    CapabilityOutcome,
    CapabilityProgram,
    CapabilityRef,
    CapabilityRun,
    CapabilityRunState,
    CapabilityStep,
    CapabilityStepKind,
    CapabilityStepRecord,
    CapabilityWaiting,
    ChildExecutionState,
    CancelCapability,
    CompensationOutcome,
    EffectState,
    InputReference,
    ResourceUsage,
    ResultReference,
    ResumeCapability,
    Retryability,
    RunCapability,
    StartCapability,
    StepOutcomeState,
    WaitReason,
)
from .ports import (
    AuthorizedChildExecutionQuery,
    CancelCapabilityResult,
    CancelChildExecution,
    CapabilityAuthorizationPort,
    CapabilityEvent,
    CapabilityStateNotFound,
    CapabilityStatePort,
    CapabilityToolCancel,
    CapabilityToolInvocation,
    ChildExecutionPort,
    CreateChildExecution,
    ChildExecutionContext,
    DefaultCapabilityAuthorization,
    ToolCancelled,
    ToolFailed,
    ToolInvocationOutcome,
    ToolLimitRequest,
    ToolSucceeded,
    ToolWaiting,
    StateConflict,
)
from .registry import CapabilityRegistry, RegistryNotFound
from .scheduler import DeterministicStepScheduler, ProgramValidationError


class CapabilityConflict(RuntimeError):
    """A stale or bounded capability state cannot be advanced."""


class CapabilityNotEligible(RuntimeError):
    """The canonical Execution is not eligible for the requested operation."""


class CapabilityService:
    """Composes typed steps while delegating all effects to public ports."""

    def __init__(
        self,
        execution_control: ExecutionControl,
        registry: CapabilityRegistry,
        tool_port,
        child_port: ChildExecutionPort,
        state: CapabilityStatePort,
        *,
        clock=None,
        authorization: CapabilityAuthorizationPort | None = None,
    ) -> None:
        self._execution_control = execution_control
        self._registry = registry
        self._tool_port = tool_port
        self._child_port = child_port
        self._state = state
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._authorization = authorization or DefaultCapabilityAuthorization()

    def start(self, request: StartCapability) -> CapabilityAccepted:
        execution_id = f"execution:capability:{uuid4()}"
        context = request.context(execution_id)
        start_key = (
            str(request.user_id), str(request.workspace_id or ""), str(request.agent_id),
            str(request.correlation_id), str(request.purpose), str(request.actor),
            str(request.capability_ref), str(request.idempotency_key),
        )
        prior = self._state.find_start(start_key)
        if prior is not None:
            return CapabilityAccepted(prior.capability_run_id, prior.context.execution_id, prior.state)
        descriptor = self._registry.resolve(request.capability_ref, context)
        self._validate_limits(request.limits, descriptor)
        if descriptor.status.value == "DISABLED":
            raise PermissionError("disabled capability cannot start")
        program = self._registry.program(request.capability_ref, context)
        self._validate_program(program, descriptor)
        now = self._clock()
        execution = Execution.create(
            execution_id=execution_id,
            ownership=Ownership(str(request.user_id), str(request.workspace_id) if request.workspace_id is not None else None),
            agent_id=str(request.agent_id),
            task=request.task,
            correlation_id=str(request.correlation_id),
            limits=ExecutionLimits(
                max_duration_seconds=request.limits.timeout_seconds,
                max_iterations=request.limits.maximum_steps,
                max_cost=request.limits.maximum_cost,
            ),
            now=now,
            causation_id=str(request.request_id),
        )
        command_context = self._execution_context(context)
        result = self._execution_control.create(
            __import__("agentos.execution.ports", fromlist=["CreateExecution"]).CreateExecution(
                context=command_context,
                command_id=str(request.request_id),
                idempotency_key=str(request.idempotency_key),
                expected_version=None,
                requested_at=now,
                execution=execution,
            )
        )
        if not isinstance(result, (Accepted,)):
            raise CapabilityConflict(f"execution creation was not confirmed: {result!r}")
        run = CapabilityRun(
            capability_run_id=f"capability-run:{uuid4()}",
            capability_ref=request.capability_ref,
            context=context,
            input_ref=InputReference(str(request.input_ref)),
            state=CapabilityRunState.QUEUED,
            state_version=1,
            current_steps=(),
            completed_steps=(),
            child_execution_ids=(),
            usage=ResourceUsage(),
            checkpoint_ref=None,
            result_ref=None,
            started_at=None,
            finished_at=None,
        )
        self._state.create(run, start_key=start_key)
        return CapabilityAccepted(run.capability_run_id, execution_id)

    def run(self, request: RunCapability) -> CapabilityOutcome:
        run = self._load(request.capability_run_id, request.context)
        self._check_version(run, request.expected_state_version)
        if run.state in {CapabilityRunState.SUCCEEDED, CapabilityRunState.FAILED, CapabilityRunState.CANCELLED}:
            return self._outcome_for_terminal(run)
        if run.cancellation_requested:
            return self._cancel_run(run, request.context, "cancel requested")
        descriptor = self._registry.resolve(run.capability_ref, run.context)
        program = self._registry.program(run.capability_ref, run.context)
        self._validate_program(program, descriptor)
        execution = self._execution_control.load(self._execution_context(run.context))
        signal = self._execution_control.current_signal(ExecutionControlQuery(self._execution_context(run.context)))
        if signal is ControlSignal.CANCEL_REQUESTED:
            return self._cancel_run(run, request.context, "kernel cancellation requested")
        if execution.state is ExecutionState.STARTING:
            self._execution_control.transition(
                TransitionExecution(
                    context=self._execution_context(run.context), command_id=f"{run.capability_run_id}:running",
                    idempotency_key=f"{run.capability_run_id}:running", expected_version=execution.state_version,
                    requested_at=self._clock(), target_state=ExecutionState.RUNNING, reason_code="capability_started",
                )
            )
            run = self._save(
                replace(run, state=CapabilityRunState.RUNNING, started_at=self._clock()),
                event=self._event(run, CapabilityEventType.STARTED),
            )
            execution = self._execution_control.load(self._execution_context(run.context))
        elif execution.state is not ExecutionState.RUNNING:
            raise CapabilityNotEligible(f"execution is not runnable: {execution.state.value}")
        return self._continue(run, descriptor, program, execution)

    def resume(self, request: ResumeCapability) -> CapabilityOutcome:
        run = self._load(request.capability_run_id, request.context)
        self._check_version(run, request.expected_state_version)
        if request.resume_from is not None:
            self._state.load_checkpoint(str(request.resume_from), request.context)
        program = self._registry.program(run.capability_ref, run.context)
        if run.state is CapabilityRunState.WAITING_CHILD:
            for child_id in run.child_execution_ids:
                child = self._child_port.inspect(
                    AuthorizedChildExecutionQuery(str(child_id), str(run.capability_run_id), run.context)
                )
                if child.state is ChildExecutionState.FAILED:
                    return self._fail_run(run, request.context, "child_execution_failed")
                if child.state is not ChildExecutionState.COMPLETED:
                    return self._waiting(run, WaitReason.CHILD)
                if child.result_ref is not None:
                    step = next(item for item in program.steps if item.step_id in run.current_steps)
                    record = CapabilityStepRecord(
                        step_id=step.step_id, attempt=1, invocation_id=None, child_execution_id=child.execution_id,
                        outcome=StepOutcomeState.SUCCEEDED, result_ref=child.result_ref,
                        effect_state=EffectState.APPLIED, finished_at=self._clock(),
                    )
                    run = self._save(
                        replace(run, state=CapabilityRunState.RUNNING, current_steps=(), completed_steps=run.completed_steps + (record,)),
                        event=self._event(run, CapabilityEventType.STEP_FINISHED, step_id=str(step.step_id), result_ref=child.result_ref, outcome="SUCCEEDED"),
                    )
            execution = self._execution_control.load(self._execution_context(run.context))
            if execution.state is ExecutionState.PAUSED:
                self._execution_control.request_resume(
                    __import__("agentos.execution.ports", fromlist=["ResumeExecution"]).ResumeExecution(
                        context=self._execution_context(run.context), command_id=f"{run.capability_run_id}:resume",
                        idempotency_key=f"{run.capability_run_id}:resume:{run.state_version}", expected_version=execution.state_version,
                        requested_at=self._clock(),
                    )
                )
            descriptor = self._registry.resolve(run.capability_ref, run.context)
            return self._continue(run, descriptor, program, self._activate_execution(run.context))
        if run.state is CapabilityRunState.PAUSED:
            execution = self._execution_control.load(self._execution_context(run.context))
            if execution.state is ExecutionState.PAUSED:
                self._execution_control.request_resume(
                    __import__("agentos.execution.ports", fromlist=["ResumeExecution"]).ResumeExecution(
                        context=self._execution_context(run.context), command_id=f"{run.capability_run_id}:resume",
                        idempotency_key=f"{run.capability_run_id}:resume:{run.state_version}", expected_version=execution.state_version,
                        requested_at=self._clock(),
                    )
                )
            descriptor = self._registry.resolve(run.capability_ref, run.context)
            return self._continue(run, descriptor, program, self._activate_execution(run.context))
        return self.run(RunCapability(run.capability_run_id, run.context, run.state_version, request.resume_from))

    def request_cancel(self, request: CancelCapability) -> CancelCapabilityResult:
        run = self._load(request.capability_run_id, request.context)
        if run.state is CapabilityRunState.CANCELLED:
            return CancelCapabilityResult(True, run.state_version)
        if run.state in {CapabilityRunState.SUCCEEDED, CapabilityRunState.FAILED}:
            return CancelCapabilityResult(False, run.state_version)
        if run.current_steps:
            step_id = run.current_steps[0]
            self._tool_port.request_cancel(CapabilityToolCancel(str(run.capability_run_id), step_id, run.context, request.reason))
        for child_id in run.child_execution_ids:
            self._child_port.request_cancel(CancelChildExecution(str(child_id), str(run.capability_run_id), run.context, request.reason))
        return_value = self._cancel_run(run, request.context, request.reason)
        current = self._state.load(str(run.capability_run_id), request.context)
        return CancelCapabilityResult(isinstance(return_value, CapabilityCancelled), current.state_version)

    def inspect(self, query) -> CapabilityRun:
        return self._load(query.capability_run_id, query.context)

    def _continue(self, run, descriptor, program, execution) -> CapabilityOutcome:
        scheduler = DeterministicStepScheduler(descriptor.limits.maximum_parallel_steps)
        completed = tuple(str(record.step_id) for record in run.completed_steps if record.outcome is StepOutcomeState.SUCCEEDED)
        ready = scheduler.ready(program, completed=completed, active=tuple(str(item) for item in run.current_steps))
        if not ready:
            if len(completed) == len(program.steps):
                return self._succeed_run(run, execution, self._result_ref(run))
            return self._fail_run(run, run.context, "dependencies_blocked")
        for step_id in ready:
            step = next(item for item in program.steps if str(item.step_id) == step_id)
            outcome = self._execute_step(run, descriptor, step, execution)
            if isinstance(outcome, CapabilityWaiting | CapabilityFailed | CapabilityCancelled):
                return outcome
            run = self._state.load(str(run.capability_run_id), run.context)
            execution = self._execution_control.load(self._execution_context(run.context))
        return self._succeed_run(run, execution, self._result_ref(run)) if len(run.completed_steps) == len(program.steps) else self._continue(
            self._state.load(str(run.capability_run_id), run.context), descriptor, program,
            self._execution_control.load(self._execution_context(run.context)),
        )

    def _activate_execution(self, context):
        execution_context = self._execution_context(context)
        execution = self._execution_control.load(execution_context)
        if execution.state is ExecutionState.QUEUED:
            self._execution_control.acquire(
                AcquireExecution(
                    context=execution_context, command_id=f"{context.execution_id}:resume-acquire",
                    idempotency_key=f"{context.execution_id}:resume-acquire:{execution.state_version}",
                    expected_version=execution.state_version, requested_at=self._clock(), worker_ref="capability-resume",
                )
            )
            execution = self._execution_control.load(execution_context)
        if execution.state is ExecutionState.STARTING:
            self._execution_control.transition(
                TransitionExecution(
                    context=execution_context, command_id=f"{context.execution_id}:resume-running",
                    idempotency_key=f"{context.execution_id}:resume-running:{execution.state_version}",
                    expected_version=execution.state_version, requested_at=self._clock(),
                    target_state=ExecutionState.RUNNING, reason_code="capability_resumed",
                )
            )
            execution = self._execution_control.load(execution_context)
        if execution.state is not ExecutionState.RUNNING:
            raise CapabilityNotEligible(f"execution cannot resume: {execution.state.value}")
        return execution

    def _execute_step(self, run, descriptor, step, execution) -> CapabilityOutcome | None:
        try:
            self._ensure_limits(run, descriptor)
        except CapabilityConflict:
            return self._fail_run(run, run.context, self._limit_error(run, descriptor))
        arguments = __import__("agentos.capabilities.models", fromlist=["StructuredValue"]).StructuredValue.from_mapping(
            {binding.name: binding.reference for binding in step.input_bindings}
        )
        if not self._authorization.authorize(run.context, descriptor, step, arguments):
            return self._fail_run(run, run.context, "step_not_authorized")
        run = self._save(
            replace(run, state=CapabilityRunState.RUNNING, current_steps=(step.step_id,)),
            event=self._event(run, CapabilityEventType.STEP_STARTED, step_id=str(step.step_id)),
        )
        if step.kind is CapabilityStepKind.CHECKPOINT:
            checkpoint = self._checkpoint(run, next_decision=str(step.step_id))
            record = CapabilityStepRecord(step.step_id, 1, None, None, StepOutcomeState.SUCCEEDED, None, EffectState.NOT_APPLIED, self._clock())
            self._save(replace(self._state.load(str(run.capability_run_id), run.context), current_steps=(), completed_steps=run.completed_steps + (record,), checkpoint_ref=checkpoint.checkpoint_ref), checkpoint=checkpoint, event=self._event(run, CapabilityEventType.CHECKPOINT_CREATED, step_id=str(step.step_id), outcome="CONFIRMED"))
            return None
        if step.kind is CapabilityStepKind.CHILD_EXECUTION:
            if run.usage.child_executions >= descriptor.limits.maximum_child_executions:
                return self._fail_run(run, run.context, "maximum_child_executions")
            child_id = self._child_port.create(
                CreateChildExecution(
                    capability_run_id=str(run.capability_run_id), step_id=step.step_id,
                    child_capability_ref=step.child_capability_ref,
                    context=ChildExecutionContext(
                        user_id=str(run.context.user_id), workspace_id=str(run.context.workspace_id) if run.context.workspace_id is not None else None,
                        agent_id=str(run.context.agent_id), parent_execution_id=str(run.context.execution_id),
                        correlation_id=str(run.context.correlation_id), purpose=str(run.context.purpose), actor=str(run.context.actor),
                    ),
                    input_refs=tuple(binding.reference for binding in step.input_bindings),
                    purpose=run.context.purpose, causation_ref=f"step:{step.step_id}", maximum_depth=1,
                )
            )
            run = self._save(
                replace(run, current_steps=(step.step_id,), child_execution_ids=run.child_execution_ids + (child_id,), usage=run.usage.plus(ResourceUsage(child_executions=1, steps=1))),
                event=self._event(run, CapabilityEventType.CHILD_EXECUTION_CREATED, step_id=str(step.step_id)),
            )
            return self._waiting(run, WaitReason.CHILD)
        if step.kind is not CapabilityStepKind.TOOL:
            record = CapabilityStepRecord(step.step_id, 1, None, None, StepOutcomeState.SUCCEEDED, None, EffectState.NOT_APPLIED, self._clock())
            self._save(replace(run, current_steps=(), completed_steps=run.completed_steps + (record,)), event=self._event(run, CapabilityEventType.STEP_FINISHED, step_id=str(step.step_id), outcome="SUCCEEDED"))
            return None
        attempt = 1 + sum(1 for item in run.completed_steps if item.step_id == step.step_id)
        invocation_id = f"invocation:{run.capability_run_id}:{step.step_id}:{attempt}"
        tool_outcome = self._tool_port.invoke(
            CapabilityToolInvocation(
                capability_run_id=str(run.capability_run_id), step_id=step.step_id, invocation_id=invocation_id,
                tool_ref=step.tool_ref, context=run.context, arguments=arguments,
                idempotency_key=f"capability:{run.capability_run_id}:{step.step_id}:{attempt}",
                limits=ToolLimitRequest(step.timeout_seconds, descriptor.limits.maximum_resource_usage),
            )
        )
        if isinstance(tool_outcome, ToolSucceeded):
            record = CapabilityStepRecord(step.step_id, attempt, invocation_id, None, StepOutcomeState.SUCCEEDED, tool_outcome.result_ref, tool_outcome.effect_state, self._clock())
            new_usage = run.usage.plus(replace(tool_outcome.usage, tool_invocations=tool_outcome.usage.tool_invocations + 1, steps=tool_outcome.usage.steps + 1))
            try:
                self._ensure_usage(new_usage, descriptor)
            except CapabilityConflict:
                self._save(replace(run, current_steps=(), completed_steps=run.completed_steps + (record,), usage=new_usage, result_ref=tool_outcome.result_ref), event=self._event(run, CapabilityEventType.STEP_FINISHED, step_id=str(step.step_id), result_ref=tool_outcome.result_ref, outcome="SUCCEEDED"))
                return self._fail_run(self._state.load(str(run.capability_run_id), run.context), run.context, self._limit_error(run, descriptor))
            self._save(replace(run, current_steps=(), completed_steps=run.completed_steps + (record,), usage=new_usage, result_ref=tool_outcome.result_ref), event=self._event(run, CapabilityEventType.STEP_FINISHED, step_id=str(step.step_id), result_ref=tool_outcome.result_ref, outcome="SUCCEEDED"))
            return None
        if isinstance(tool_outcome, ToolWaiting):
            return self._waiting(run, WaitReason.TOOL)
        if isinstance(tool_outcome, ToolCancelled):
            return self._cancel_run(run, run.context, tool_outcome.reason)
        if isinstance(tool_outcome, ToolFailed):
            record = CapabilityStepRecord(step.step_id, attempt, invocation_id, None, StepOutcomeState.UNKNOWN if tool_outcome.effect_state is EffectState.UNKNOWN else StepOutcomeState.FAILED, tool_outcome.result_ref, tool_outcome.effect_state, self._clock(), tool_outcome.error_code)
            self._save(replace(run, current_steps=(), completed_steps=run.completed_steps + (record,)), event=self._event(run, CapabilityEventType.STEP_FINISHED, step_id=str(step.step_id), result_ref=tool_outcome.result_ref, outcome=record.outcome.value))
            if tool_outcome.retryability is Retryability.SAFE and tool_outcome.effect_state is not EffectState.UNKNOWN and attempt < step.retry_policy.maximum_attempts:
                return self._continue(self._state.load(str(run.capability_run_id), run.context), descriptor, self._registry.program(run.capability_ref, run.context), execution)
            return self._fail_run(self._state.load(str(run.capability_run_id), run.context), run.context, tool_outcome.error_code)
        return self._fail_run(run, run.context, "invalid_tool_outcome")

    def _waiting(self, run, reason: WaitReason) -> CapabilityWaiting:
        checkpoint = self._checkpoint(replace(run, state=CapabilityRunState.WAITING_CHILD if reason is WaitReason.CHILD else CapabilityRunState.WAITING_TOOL), next_decision=reason.value)
        current = self._state.load(str(run.capability_run_id), run.context)
        waiting = replace(current, state=CapabilityRunState.WAITING_CHILD if reason is WaitReason.CHILD else CapabilityRunState.WAITING_TOOL, checkpoint_ref=checkpoint.checkpoint_ref)
        waiting = self._save(waiting, checkpoint=checkpoint, event=self._event(current, CapabilityEventType.CHECKPOINT_CREATED, outcome=reason.value))
        execution = self._execution_control.load(self._execution_context(run.context))
        target = ExecutionState.PAUSED if reason is WaitReason.CHILD else ExecutionState.WAITING_TOOL
        self._execution_control.transition(
            TransitionExecution(
                context=self._execution_context(run.context), command_id=f"{run.capability_run_id}:wait:{reason.value}",
                idempotency_key=f"{run.capability_run_id}:wait:{reason.value}:{execution.state_version}", expected_version=execution.state_version,
                requested_at=self._clock(), target_state=target, reason_code=f"capability_waiting_{reason.value.lower()}",
            )
        )
        return __import__("agentos.capabilities.models", fromlist=["CapabilityWaiting"]).CapabilityWaiting(reason, checkpoint.checkpoint_ref)

    def _checkpoint(self, run, *, next_decision: str | None = None) -> CapabilityCheckpoint:
        checkpoint = CapabilityCheckpoint(
            checkpoint_ref=f"checkpoint:{run.capability_run_id}:{run.state_version + 1}",
            capability_run_id=run.capability_run_id, descriptor_ref=run.capability_ref, state=run.state,
            completed_steps=run.completed_steps, current_steps=run.current_steps,
            child_execution_ids=run.child_execution_ids, usage=run.usage, next_decision=next_decision, created_at=self._clock(),
        )
        return checkpoint

    def _succeed_run(self, run, execution, result_ref):
        result_ref = result_ref or f"result:{run.capability_run_id}"
        terminal = replace(run, state=CapabilityRunState.SUCCEEDED, current_steps=(), result_ref=result_ref, finished_at=self._clock())
        terminal = self._save(terminal, event=self._event(run, CapabilityEventType.FINISHED, result_ref=result_ref, outcome="SUCCEEDED"))
        self._execution_control.commit(
            CommitExecutionChanges(
                context=self._execution_context(run.context), command_id=f"{run.capability_run_id}:complete",
                idempotency_key=f"{run.capability_run_id}:complete", expected_version=execution.state_version,
                requested_at=self._clock(), expected_state=execution.state, target_state=ExecutionState.COMPLETED,
                reason_code="capability_finished", result_ref=result_ref,
                changes=(ExecutionRelatedChange(kind="capability-usage", iterations=terminal.usage.steps, cost=terminal.usage.cost),),
            )
        )
        return __import__("agentos.capabilities.models", fromlist=["CapabilitySucceeded"]).CapabilitySucceeded(result_ref, terminal.usage)

    def _fail_run(self, run, context, error_code):
        descriptor = self._registry.resolve(run.capability_ref, run.context)
        compensation = None
        if descriptor.compensation_policy.value == "EXPLICIT_STEPS" and any(item.effect_state is EffectState.APPLIED for item in run.completed_steps):
            compensation = self._compensate(run, descriptor)
            run = self._state.load(str(run.capability_run_id), run.context)
        terminal = replace(run, state=CapabilityRunState.FAILED, current_steps=(), finished_at=self._clock())
        terminal = self._save(terminal, event=self._event(run, CapabilityEventType.FAILED, outcome=error_code, reason="COMPENSATION_INCOMPLETE" if compensation is not None and not compensation.complete else None))
        execution = self._execution_control.load(self._execution_context(context))
        if execution.state not in {ExecutionState.FAILED, ExecutionState.CANCELLED, ExecutionState.COMPLETED}:
            self._execution_control.transition(
                TransitionExecution(
                    context=self._execution_context(context), command_id=f"{run.capability_run_id}:failed",
                    idempotency_key=f"{run.capability_run_id}:failed", expected_version=execution.state_version,
                    requested_at=self._clock(), target_state=ExecutionState.FAILED, reason_code=error_code,
                    failure=ExecutionFailure(code="RUNTIME_ERROR", detail_ref=error_code),
                )
            )
        return CapabilityFailed(error_code, compensation)

    def _cancel_run(self, run, context, reason):
        descriptor = self._registry.resolve(run.capability_ref, run.context)
        compensation = None
        if descriptor.compensation_policy.value == "EXPLICIT_STEPS" and any(item.effect_state is EffectState.APPLIED for item in run.completed_steps):
            compensation = self._compensate(run, descriptor)
            run = self._state.load(str(run.capability_run_id), run.context)
        terminal = replace(run, state=CapabilityRunState.CANCELLED, current_steps=(), finished_at=self._clock())
        terminal = self._save(terminal, event=self._event(run, CapabilityEventType.CANCELLED, reason=reason, outcome="CANCELLED"))
        execution = self._execution_control.load(self._execution_context(context))
        if execution.state not in {ExecutionState.CANCELLED, ExecutionState.COMPLETED, ExecutionState.FAILED}:
            self._execution_control.request_cancel(
                CancelExecution(
                    context=self._execution_context(context), command_id=f"{run.capability_run_id}:cancel",
                    idempotency_key=f"{run.capability_run_id}:cancel", expected_version=execution.state_version,
                    requested_at=self._clock(), reason=CancellationReason(CancellationReasonCode.USER_REQUESTED, reason),
                )
            )
        return CapabilityCancelled(reason, compensation)

    def _compensate(self, run, descriptor):
        program = self._registry.program(run.capability_ref, run.context)
        current = self._save(replace(run, state=CapabilityRunState.COMPENSATING, current_steps=()), event=None)
        records = []
        complete = True
        for index, step in enumerate(program.compensation_steps, start=1):
            arguments = __import__("agentos.capabilities.models", fromlist=["StructuredValue"]).StructuredValue.from_mapping(
                {binding.name: binding.reference for binding in step.input_bindings}
            )
            if not self._authorization.authorize(run.context, descriptor, step, arguments):
                records.append(CapabilityStepRecord(step.step_id, index, None, None, StepOutcomeState.FAILED, None, EffectState.NOT_APPLIED, self._clock(), "compensation_not_authorized"))
                complete = False
                break
            invocation_id = f"compensation:{run.capability_run_id}:{step.step_id}:{index}"
            outcome = self._tool_port.invoke(
                CapabilityToolInvocation(
                    capability_run_id=str(run.capability_run_id), step_id=step.step_id, invocation_id=invocation_id,
                    tool_ref=step.tool_ref, context=run.context, arguments=arguments,
                    idempotency_key=f"compensation:{run.capability_run_id}:{step.step_id}:{index}",
                    limits=ToolLimitRequest(step.timeout_seconds, descriptor.limits.maximum_resource_usage),
                )
            )
            if isinstance(outcome, ToolSucceeded):
                records.append(CapabilityStepRecord(step.step_id, index, invocation_id, None, StepOutcomeState.SUCCEEDED, outcome.result_ref, outcome.effect_state, self._clock()))
            else:
                effect_state = outcome.effect_state if isinstance(outcome, (ToolFailed, ToolCancelled)) else EffectState.UNKNOWN
                error_code = outcome.error_code if isinstance(outcome, ToolFailed) else "compensation_cancelled"
                records.append(CapabilityStepRecord(step.step_id, index, invocation_id, None, StepOutcomeState.UNKNOWN if effect_state is EffectState.UNKNOWN else StepOutcomeState.FAILED, getattr(outcome, "result_ref", None), effect_state, self._clock(), error_code))
                complete = False
                break
        self._save(
            replace(current, state=CapabilityRunState.COMPENSATING, current_steps=(), completed_steps=current.completed_steps + tuple(records)),
            event=self._event(current, CapabilityEventType.COMPENSATION_FINISHED, outcome="SUCCEEDED" if complete else "FAILED"),
        )
        return CompensationOutcome(tuple(records), complete)

    def _outcome_for_terminal(self, run):
        if run.state is CapabilityRunState.SUCCEEDED:
            return __import__("agentos.capabilities.models", fromlist=["CapabilitySucceeded"]).CapabilitySucceeded(run.result_ref, run.usage)
        if run.state is CapabilityRunState.CANCELLED:
            return CapabilityCancelled("cancelled", None)
        return CapabilityFailed("terminal_failure", None)

    def _load(self, run_id, context):
        return self._state.load(str(run_id), context)

    @staticmethod
    def _check_version(run, expected):
        if run.state_version != expected:
            raise CapabilityConflict(f"stale capability state version: expected {expected}, current {run.state_version}")

    def _save(self, run, *, checkpoint=None, event=None):
        return self._state.save(run, expected_version=run.state_version, checkpoint=checkpoint, event=event)

    def _event(self, run, event_type, *, step_id=None, result_ref=None, outcome=None, reason=None):
        return CapabilityEvent(event_type, str(run.capability_run_id), run.capability_ref, str(run.context.execution_id), str(run.context.correlation_id), run.state_version + 1, self._clock(), step_id, result_ref, outcome, reason)

    @staticmethod
    def _execution_context(context):
        return ExecutionCommandContext(
            user_id=str(context.user_id), workspace_id=str(context.workspace_id) if context.workspace_id is not None else None,
            agent_id=str(context.agent_id), execution_id=str(context.execution_id), correlation_id=str(context.correlation_id), purpose=str(context.purpose),
        )

    @staticmethod
    def _validate_limits(requested, descriptor):
        maximum = descriptor.limits
        pairs = (("timeout_seconds", requested.timeout_seconds, maximum.timeout_seconds), ("maximum_steps", requested.maximum_steps, maximum.maximum_steps), ("maximum_tool_invocations", requested.maximum_tool_invocations, maximum.maximum_tool_invocations), ("maximum_child_executions", requested.maximum_child_executions, maximum.maximum_child_executions), ("maximum_parallel_steps", requested.maximum_parallel_steps, maximum.maximum_parallel_steps), ("maximum_resource_usage", requested.maximum_resource_usage, maximum.maximum_resource_usage))
        if any(value > limit for _, value, limit in pairs) or (maximum.maximum_cost is not None and (requested.maximum_cost is None or requested.maximum_cost > maximum.maximum_cost)):
            raise ValueError("requested capability limits exceed descriptor limits")

    @staticmethod
    def _validate_program(program: CapabilityProgram, descriptor: CapabilityDescriptor):
        DeterministicStepScheduler(descriptor.limits.maximum_parallel_steps).validate(program)
        if len(program.steps) > descriptor.limits.maximum_steps:
            raise ValueError("program exceeds maximum_steps")
        for step in program.steps:
            if step.tool_ref is not None and step.tool_ref not in descriptor.allowed_tools:
                raise PermissionError("step Tool is outside descriptor allowlist")
            if step.child_capability_ref is not None and step.child_capability_ref not in descriptor.allowed_child_capabilities:
                raise PermissionError("child Capability is outside descriptor allowlist")
            if step.child_capability_ref == descriptor.capability_ref:
                raise ValueError("recursive Capability composition is forbidden")

    @staticmethod
    def _ensure_limits(run, descriptor):
        if run.usage.steps >= descriptor.limits.maximum_steps or run.usage.tool_invocations >= descriptor.limits.maximum_tool_invocations or run.usage.child_executions >= descriptor.limits.maximum_child_executions or run.usage.resource_units > descriptor.limits.maximum_resource_usage or (descriptor.limits.maximum_cost is not None and run.usage.cost > descriptor.limits.maximum_cost):
            raise CapabilityConflict("capability limit exhausted before next effect")

    @staticmethod
    def _ensure_usage(usage, descriptor):
        if usage.steps > descriptor.limits.maximum_steps or usage.tool_invocations > descriptor.limits.maximum_tool_invocations or usage.resource_units > descriptor.limits.maximum_resource_usage or (descriptor.limits.maximum_cost is not None and usage.cost > descriptor.limits.maximum_cost):
            raise CapabilityConflict("capability limit exceeded after effect")

    @staticmethod
    def _limit_error(run, descriptor):
        if run.usage.tool_invocations >= descriptor.limits.maximum_tool_invocations:
            return "maximum_tool_invocations"
        if run.usage.child_executions >= descriptor.limits.maximum_child_executions:
            return "maximum_child_executions"
        if run.usage.steps >= descriptor.limits.maximum_steps:
            return "maximum_steps"
        if descriptor.limits.maximum_cost is not None and run.usage.cost >= descriptor.limits.maximum_cost:
            return "maximum_cost"
        return "maximum_resource_usage"

    @staticmethod
    def _result_ref(run):
        return run.result_ref or (run.completed_steps[-1].result_ref if run.completed_steps and run.completed_steps[-1].result_ref is not None else f"result:{run.capability_run_id}")

__all__ = ["CapabilityService", "CapabilityConflict", "CapabilityNotEligible"]
