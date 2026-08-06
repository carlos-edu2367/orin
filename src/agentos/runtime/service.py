from __future__ import annotations

from dataclasses import replace
from threading import Lock
from uuid import uuid4

from agentos.execution.models import (
    CancellationReason,
    CancellationReasonCode,
    Execution,
    ExecutionFailure,
    ExecutionState,
    FailureReason,
)
from agentos.execution.ports import (
    AcquireExecution,
    CancelExecution,
    CommitExecutionChanges,
    ControlSignal,
    ExecutionCommandContext,
    ExecutionControl,
    ExecutionControlQuery,
    ExecutionRelatedChange,
    Rejected,
    TransitionExecution,
)

from .models import (
    ActionCancelled,
    ActionFailed,
    ActionRequest,
    ActionSucceeded,
    BudgetDecision,
    BudgetRequest,
    CancelledOutcome,
    CompletedOutcome,
    ContextAssemblyRequest,
    ContextTurnUpdate,
    FailedOutcome,
    ModelResolveRequest,
    ProviderCancelled,
    ProviderFailed,
    ProviderFinal,
    ProviderToolRequest,
    ProviderUserInputRequest,
    RuntimeErrorCategory,
    RuntimeErrorInfo,
    RuntimeLimits,
    RuntimeOutcome,
    RuntimeRequest,
    RuntimeUsage,
    WaitingOutcome,
)
from .ports import (
    BudgetPolicy,
    CheckpointPort,
    Clock,
    ContextManager,
    ModelResolver,
    ProviderPort,
    ToolCapabilityPort,
)


class RuntimeService:
    """Synchronous RFC 101 execution loop over public domain ports."""

    def __init__(
        self,
        *,
        control: ExecutionControl,
        context_manager: ContextManager,
        model_resolver: ModelResolver,
        provider: ProviderPort,
        action_port: ToolCapabilityPort,
        checkpoint_port: CheckpointPort,
        clock: Clock,
        budget_policy: BudgetPolicy,
        limits: RuntimeLimits | None = None,
    ) -> None:
        self._control = control
        self._context_manager = context_manager
        self._model_resolver = model_resolver
        self._provider = provider
        self._action_port = action_port
        self._checkpoint_port = checkpoint_port
        self._clock = clock
        self._budget_policy = budget_policy
        self._limits = limits
        self._active = Lock()

    def execute(self, request: RuntimeRequest) -> RuntimeOutcome:
        if not self._active.acquire(blocking=False):
            return FailedOutcome(
                request.execution_id,
                RuntimeErrorInfo(
                    RuntimeErrorCategory.CONCURRENCY,
                    "RUNTIME_ALREADY_EXECUTING",
                ),
            )
        try:
            outcome = self._execute(request)
            disposition = self._disposition(outcome)
            if disposition is not None:
                try:
                    self._context_manager.finalize(request.execution_id, disposition)
                except Exception:
                    pass
            return outcome
        finally:
            self._active.release()

    def _execute(self, request: RuntimeRequest) -> RuntimeOutcome:
        started_monotonic = self._clock.monotonic()
        try:
            execution, context = self._load(request)
        except (LookupError, PermissionError):
            return self._failure(
                request.execution_id,
                RuntimeErrorCategory.INITIALIZATION,
                "EXECUTION_ACCESS_DENIED",
            )
        terminal = self._terminal_outcome(execution)
        if terminal is not None:
            return terminal

        signal = self._signal(context)
        if execution.state is ExecutionState.QUEUED:
            if signal is ControlSignal.CANCEL_REQUESTED:
                return self._cancel(execution, context)
            acquisition = self._control.acquire(
                AcquireExecution(
                    context=context,
                    command_id=self._id("acquire"),
                    idempotency_key=self._id("acquire-key"),
                    expected_version=execution.state_version,
                    requested_at=self._clock.now(),
                    worker_ref=request.worker_ref,
                )
            )
            if isinstance(acquisition, Rejected):
                return self._failure(request.execution_id, RuntimeErrorCategory.INITIALIZATION, "ACQUISITION_REJECTED")
            execution, context = self._load(request)

        if execution.state is ExecutionState.STARTING:
            started = self._control.transition(
                TransitionExecution(
                    context=context,
                    command_id=self._id("start"),
                    idempotency_key=self._id("start-key"),
                    expected_version=execution.state_version,
                    requested_at=self._clock.now(),
                    target_state=ExecutionState.RUNNING,
                    reason_code="runtime_started",
                )
            )
            if isinstance(started, Rejected):
                return self._failure(request.execution_id, RuntimeErrorCategory.INITIALIZATION, "START_REJECTED")
            execution, context = self._load(request)

        checkpoint = None
        if request.resume_from is not None:
            try:
                checkpoint = self._checkpoint_port.load(request.resume_from, context)
            except Exception:
                return self._fail_execution(
                    execution,
                    context,
                    RuntimeErrorInfo(RuntimeErrorCategory.CHECKPOINT, "CHECKPOINT_LOAD_FAILED"),
                )
            if (
                checkpoint.execution_id != execution.execution_id
                or checkpoint.state_version > execution.state_version
            ):
                return self._fail_execution(
                    execution,
                    context,
                    RuntimeErrorInfo(RuntimeErrorCategory.CHECKPOINT, "CHECKPOINT_INCOMPATIBLE"),
            )
            execution = replace(execution, context_manifest_ref=checkpoint.context_manifest_ref)

        if execution.state is ExecutionState.WAITING_TOOL:
            if checkpoint is None or checkpoint.pending_action_ref is not None:
                return self._fail_execution(
                    execution,
                    context,
                    RuntimeErrorInfo(RuntimeErrorCategory.RECONCILIATION, "ACTION_RECONCILIATION_REQUIRED"),
                )
            resumed = self._control.transition(
                TransitionExecution(
                    context=context,
                    command_id=self._id("recover-action"),
                    idempotency_key=self._id("recover-action-key"),
                    expected_version=execution.state_version,
                    requested_at=self._clock.now(),
                    target_state=ExecutionState.RUNNING,
                    reason_code="action_already_confirmed",
                )
            )
            if isinstance(resumed, Rejected):
                return self._failure(request.execution_id, RuntimeErrorCategory.RECONCILIATION, "ACTION_RESUME_REJECTED")
            execution, context = self._load(request)

        if execution.state in {ExecutionState.WAITING_USER, ExecutionState.PAUSED}:
            return WaitingOutcome(request.execution_id, execution.state)
        if execution.state is not ExecutionState.RUNNING:
            return self._failure(request.execution_id, RuntimeErrorCategory.INITIALIZATION, "STATE_NOT_RUNNABLE")

        if self._signal(context) is ControlSignal.CANCEL_REQUESTED:
            return self._cancel(execution, context)
        if self._signal(context) is ControlSignal.PAUSE_REQUESTED:
            return self._pause(execution, context)

        return self._run_loop(request, execution, context, started_monotonic)

    def _run_loop(
        self,
        request: RuntimeRequest,
        execution: Execution,
        context: ExecutionCommandContext,
        started_monotonic: float,
    ) -> RuntimeOutcome:
        usage = RuntimeUsage(
            duration_seconds=execution.usage.duration_seconds,
            iterations=execution.usage.iterations,
            provider_tokens=execution.usage.provider_tokens,
            cost=execution.usage.cost,
        )
        limits = self._effective_limits(execution)
        if (
            limits.max_duration_seconds is not None
            and self._clock.monotonic() - started_monotonic > limits.max_duration_seconds
        ):
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.EXECUTION_TIMEOUT, "EXECUTION_DEADLINE_EXCEEDED"),
            )
        precondition = self._pre_effect_budget(request, execution, context, usage)
        if precondition is not None:
            return precondition
        turn = execution.iteration_count + 1
        try:
            context_snapshot = self._context_manager.assemble(
                ContextAssemblyRequest(
                    context=self._operation_context(request, execution),
                    turn=turn,
                    task_ref=execution.task.task_ref,
                    model_requirements_ref=request.model_requirements_ref,
                    prior_manifest_ref=execution.context_manifest_ref,
                )
            )
        except Exception:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.CONTEXT, "CONTEXT_ASSEMBLY_FAILED"),
            )
        try:
            selection = self._model_resolver.resolve(
                ModelResolveRequest(
                    context=self._operation_context(request, execution),
                    requirements_ref=request.model_requirements_ref,
                )
            )
        except Exception:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.MODEL_RESOLUTION, "MODEL_RESOLUTION_FAILED"),
            )
        try:
            provider_outcome = self._provider.generate(
                self._provider_request(request, execution, context_snapshot, selection, usage)
            )
        except TimeoutError:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.PROVIDER_TIMEOUT, "PROVIDER_TIMEOUT"),
            )
        except Exception:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.PROVIDER, "PROVIDER_FAILED"),
            )
        if self._signal(context) is ControlSignal.CANCEL_REQUESTED:
            return self._cancel(execution, context)
        if self._signal(context) is ControlSignal.PAUSE_REQUESTED:
            return self._pause(execution, context)

        provider_usage = self._provider_delta(provider_outcome.usage)
        if isinstance(provider_outcome, ProviderFinal):
            final_usage = usage.plus(provider_usage)
            postcondition = self._post_effect_budget(
                request, execution, context, final_usage, provider_usage
            )
            if postcondition is not None:
                return postcondition
            try:
                self._context_manager.apply_turn(
                    ContextTurnUpdate(
                        context=self._operation_context(request, execution),
                        turn=turn,
                        context_ref=context_snapshot.context_ref,
                        manifest_ref=context_snapshot.manifest_ref,
                        provider_result_ref=provider_outcome.result_ref,
                    )
                )
            except Exception:
                return self._fail_execution(
                    execution,
                    context,
                    RuntimeErrorInfo(RuntimeErrorCategory.CONTEXT, "CONTEXT_UPDATE_FAILED"),
                    provider_outcome.usage,
                )
            result = self._control.commit(
                CommitExecutionChanges(
                    context=context,
                    command_id=self._id("complete"),
                    idempotency_key=self._id("complete-key"),
                    expected_version=execution.state_version,
                    requested_at=self._clock.now(),
                    expected_state=ExecutionState.RUNNING,
                    target_state=ExecutionState.COMPLETED,
                    reason_code="result_confirmed",
                    result_ref=provider_outcome.result_ref,
                    changes=self._changes_with_manifest(
                        provider_usage, context_snapshot.manifest_ref
                    ),
                )
            )
            if isinstance(result, Rejected):
                return self._failure(request.execution_id, RuntimeErrorCategory.RECONCILIATION, "RESULT_COMMIT_REJECTED")
            return CompletedOutcome(request.execution_id, provider_outcome.result_ref, final_usage)

        if isinstance(provider_outcome, ProviderToolRequest):
            next_usage = usage.plus(provider_usage)
            postcondition = self._post_effect_budget(
                request, execution, context, next_usage, provider_usage
            )
            if postcondition is not None:
                return postcondition
            waiting = self._control.commit(
                CommitExecutionChanges(
                    context=context,
                    command_id=self._id("wait-tool"),
                    idempotency_key=self._id("wait-tool-key"),
                    expected_version=execution.state_version,
                    requested_at=self._clock.now(),
                    expected_state=ExecutionState.RUNNING,
                    target_state=ExecutionState.WAITING_TOOL,
                    reason_code="action_requested",
                    changes=self._changes_with_manifest(
                        provider_usage, context_snapshot.manifest_ref
                    ),
                )
            )
            if isinstance(waiting, Rejected):
                return self._failure(request.execution_id, RuntimeErrorCategory.RECONCILIATION, "WAIT_TOOL_REJECTED")
            waiting_execution, waiting_context = self._load(request)
            try:
                action = self._action_port.invoke(
                    ActionRequest(
                        context=self._operation_context(request, waiting_execution),
                        action_ref=provider_outcome.action_ref,
                        invocation_ref=provider_outcome.invocation_ref,
                        idempotency_key=self._id("action-key"),
                    )
                )
            except TimeoutError:
                return self._fail_execution(
                    waiting_execution,
                    waiting_context,
                    RuntimeErrorInfo(RuntimeErrorCategory.ACTION_TIMEOUT, "ACTION_TIMEOUT"),
                )
            except Exception:
                return self._fail_execution(
                    waiting_execution,
                    waiting_context,
                    RuntimeErrorInfo(RuntimeErrorCategory.ACTION, "ACTION_FAILED"),
                )
            if self._signal(waiting_context) is ControlSignal.CANCEL_REQUESTED:
                return self._cancel(waiting_execution, waiting_context)
            if isinstance(action, ActionCancelled):
                return self._cancel(waiting_execution, waiting_context, action.reason)
            if isinstance(action, ActionFailed):
                self._control.commit(
                    CommitExecutionChanges(
                        context=waiting_context,
                        command_id=self._id("action-failed"),
                        idempotency_key=self._id("action-failed-key"),
                        expected_version=waiting_execution.state_version,
                        requested_at=self._clock.now(),
                        expected_state=ExecutionState.WAITING_TOOL,
                        target_state=ExecutionState.FAILED,
                        reason_code="action_failed",
                        failure=ExecutionFailure(FailureReason.RUNTIME_ERROR),
                        changes=(self._usage_change(action.usage),),
                    )
                )
                return FailedOutcome(request.execution_id, action.error)
            if isinstance(action, ActionSucceeded):
                waiting_execution, waiting_context = self._load(request)
                try:
                    action_context_snapshot = self._context_manager.apply_turn(
                        ContextTurnUpdate(
                            context=self._operation_context(request, waiting_execution),
                            turn=turn,
                            context_ref=context_snapshot.context_ref,
                            manifest_ref=context_snapshot.manifest_ref,
                            action_result_ref=action.result_ref,
                        )
                    )
                except Exception:
                    return self._fail_execution(
                        waiting_execution,
                        waiting_context,
                        RuntimeErrorInfo(RuntimeErrorCategory.CONTEXT, "CONTEXT_UPDATE_FAILED"),
                        action.usage,
                    )
                resumed = self._control.commit(
                    CommitExecutionChanges(
                        context=waiting_context,
                        command_id=self._id("action-complete"),
                        idempotency_key=self._id("action-complete-key"),
                        expected_version=waiting_execution.state_version,
                        requested_at=self._clock.now(),
                        expected_state=ExecutionState.WAITING_TOOL,
                        target_state=ExecutionState.RUNNING,
                        reason_code="action_reconciled",
                        changes=(
                            ExecutionRelatedChange(
                                kind="action-result-recorded",
                                reference=action.result_ref,
                                duration_seconds=action.usage.duration_seconds,
                                provider_tokens=action.usage.provider_tokens,
                                cost=action.usage.cost,
                            ),
                            self._context_change(action_context_snapshot.manifest_ref),
                        ),
                    )
                    )
                if isinstance(resumed, Rejected):
                    return self._failure(request.execution_id, RuntimeErrorCategory.RECONCILIATION, "ACTION_RESUME_REJECTED")
                next_execution, next_context = self._load(request)
                return self._run_loop(request, next_execution, next_context, started_monotonic)
            return self._failure(request.execution_id, RuntimeErrorCategory.ACTION, "UNKNOWN_ACTION_OUTCOME")

        if isinstance(provider_outcome, ProviderUserInputRequest):
            transition = self._control.transition(
                TransitionExecution(
                    context=context,
                    command_id=self._id("wait-user"),
                    idempotency_key=self._id("wait-user-key"),
                    expected_version=execution.state_version,
                    requested_at=self._clock.now(),
                    target_state=ExecutionState.WAITING_USER,
                    reason_code="input_required",
                )
            )
            if isinstance(transition, Rejected):
                return self._failure(request.execution_id, RuntimeErrorCategory.RECONCILIATION, "WAIT_USER_REJECTED")
            return WaitingOutcome(request.execution_id, ExecutionState.WAITING_USER)

        if isinstance(provider_outcome, ProviderCancelled):
            return self._cancel(execution, context, provider_outcome.reason)
        if isinstance(provider_outcome, ProviderFailed):
            return self._fail_execution(
                execution,
                context,
                provider_outcome.error,
                provider_usage,
            )
        return self._failure(request.execution_id, RuntimeErrorCategory.PROVIDER, "UNKNOWN_PROVIDER_OUTCOME")

    def _load(self, request: RuntimeRequest) -> tuple[Execution, ExecutionCommandContext]:
        context = ExecutionCommandContext(
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            purpose=request.purpose,
        )
        execution = self._control.load(context)
        if (
            execution.execution_id != request.execution_id
            or execution.ownership.user_id != request.user_id
            or execution.ownership.workspace_id != request.workspace_id
            or execution.agent_id != request.agent_id
            or execution.correlation_id != request.correlation_id
        ):
            raise PermissionError("execution ownership mismatch")
        return execution, context

    def _signal(self, context: ExecutionCommandContext) -> ControlSignal:
        return self._control.current_signal(ExecutionControlQuery(context=context))

    def _cancel(
        self,
        execution: Execution,
        context: ExecutionCommandContext,
        reason: CancellationReason | None = None,
    ) -> CancelledOutcome:
        cancellation = reason or CancellationReason(CancellationReasonCode.USER_REQUESTED)
        result = self._control.request_cancel(
            CancelExecution(
                context=context,
                command_id=self._id("cancel"),
                idempotency_key=self._id("cancel-key"),
                expected_version=execution.state_version,
                requested_at=self._clock.now(),
                reason=cancellation,
            )
        )
        if isinstance(result, Rejected):
            return CancelledOutcome(execution.execution_id, cancellation)
        return CancelledOutcome(execution.execution_id, cancellation)

    def _pause(self, execution: Execution, context: ExecutionCommandContext) -> WaitingOutcome:
        checkpoint_ref = f"checkpoint:{execution.execution_id}:{execution.iteration_count + 1}"
        result = self._control.commit(
            CommitExecutionChanges(
                context=context,
                command_id=self._id("pause"),
                idempotency_key=self._id("pause-key"),
                expected_version=execution.state_version,
                requested_at=self._clock.now(),
                expected_state=execution.state,
                target_state=ExecutionState.PAUSED,
                reason_code="safe_pause",
                changes=(
                    ExecutionRelatedChange(
                        kind="checkpoint-recorded",
                        reference=checkpoint_ref,
                    ),
                ),
            )
        )
        if isinstance(result, Rejected):
            return WaitingOutcome(execution.execution_id, ExecutionState.PAUSED)
        return WaitingOutcome(execution.execution_id, ExecutionState.PAUSED)

    def _terminal_outcome(self, execution: Execution) -> RuntimeOutcome | None:
        if execution.state is ExecutionState.COMPLETED and execution.result is not None:
            return CompletedOutcome(
                execution.execution_id,
                execution.result.result_ref,
                RuntimeUsage(
                    execution.usage.duration_seconds,
                    execution.usage.iterations,
                    execution.usage.provider_tokens,
                    execution.usage.cost,
                ),
            )
        if execution.state is ExecutionState.FAILED and execution.failure is not None:
            return FailedOutcome(
                execution.execution_id,
                RuntimeErrorInfo(RuntimeErrorCategory.PROVIDER, execution.failure.code.value),
            )
        if execution.state is ExecutionState.CANCELLED and execution.cancellation_reason is not None:
            return CancelledOutcome(execution.execution_id, execution.cancellation_reason)
        return None

    def _failure(self, execution_id, category, code) -> FailedOutcome:
        return FailedOutcome(execution_id, RuntimeErrorInfo(category, code))

    @staticmethod
    def _disposition(outcome):
        if isinstance(outcome, CompletedOutcome):
            return ExecutionState.COMPLETED
        if isinstance(outcome, WaitingOutcome):
            return outcome.state
        if isinstance(outcome, FailedOutcome):
            return ExecutionState.FAILED
        if isinstance(outcome, CancelledOutcome):
            return ExecutionState.CANCELLED
        return None

    def _effective_limits(self, execution):
        return self._limits or RuntimeLimits(
            max_duration_seconds=execution.limits.max_duration_seconds,
            max_iterations=execution.limits.max_iterations,
            max_cost=execution.limits.max_cost,
            max_provider_tokens=execution.limits.max_provider_tokens,
        )

    def _pre_effect_budget(self, request, execution, context, usage):
        limits = self._effective_limits(execution)
        if limits.max_iterations is not None and usage.iterations >= limits.max_iterations:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "ITERATION_LIMIT_REACHED"),
            )
        if limits.max_cost is not None and usage.cost >= limits.max_cost:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "COST_LIMIT_REACHED"),
            )
        if (
            limits.max_provider_tokens is not None
            and usage.provider_tokens >= limits.max_provider_tokens
        ):
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "TOKEN_LIMIT_REACHED"),
            )
        evaluation = self._budget_policy.evaluate(
            BudgetRequest(
                context=self._operation_context(request, execution),
                limits=limits,
                usage=usage,
                effect="provider",
            )
        )
        if evaluation.decision is BudgetDecision.FAIL:
            return self._fail_execution(
                execution,
                context,
                evaluation.error
                or RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "BUDGET_POLICY_REJECTED"),
            )
        if evaluation.decision is BudgetDecision.PAUSE:
            return self._pause(execution, context)
        return None

    def _post_effect_budget(self, request, execution, context, usage, delta):
        limits = self._effective_limits(execution)
        if limits.max_iterations is not None and usage.iterations > limits.max_iterations:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "ITERATION_LIMIT_EXCEEDED"),
                delta,
            )
        if limits.max_cost is not None and usage.cost > limits.max_cost:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "COST_LIMIT_EXCEEDED"),
                delta,
            )
        if limits.max_provider_tokens is not None and usage.provider_tokens > limits.max_provider_tokens:
            return self._fail_execution(
                execution,
                context,
                RuntimeErrorInfo(RuntimeErrorCategory.LIMIT, "TOKEN_LIMIT_EXCEEDED"),
                delta,
            )
        return None

    def _fail_execution(self, execution, context, error, usage=RuntimeUsage()):
        failure_code = (
            FailureReason.TIMEOUT
            if error.category
            in {
                RuntimeErrorCategory.EXECUTION_TIMEOUT,
                RuntimeErrorCategory.PROVIDER_TIMEOUT,
                RuntimeErrorCategory.ACTION_TIMEOUT,
                RuntimeErrorCategory.USER_WAIT_TIMEOUT,
            }
            else FailureReason.RUNTIME_ERROR
        )
        result = self._control.commit(
            CommitExecutionChanges(
                context=context,
                command_id=self._id("fail"),
                idempotency_key=self._id("fail-key"),
                expected_version=execution.state_version,
                requested_at=self._clock.now(),
                expected_state=execution.state,
                target_state=ExecutionState.FAILED,
                reason_code=error.code,
                failure=ExecutionFailure(failure_code, error.detail_ref),
                changes=(self._usage_change(usage),)
                if usage != RuntimeUsage()
                else (),
            )
        )
        return FailedOutcome(execution.execution_id, error)

    def _operation_context(self, request, execution):
        from .models import OperationContext

        return OperationContext(
            user_id=execution.ownership.user_id,
            workspace_id=execution.ownership.workspace_id,
            agent_id=execution.agent_id,
            execution_id=execution.execution_id,
            correlation_id=execution.correlation_id,
            purpose=request.purpose,
            actor_ref=request.actor_ref,
        )

    def _provider_request(self, request, execution, snapshot, selection, usage):
        from .models import InvocationReference, ProviderRequest

        return ProviderRequest(
            context=self._operation_context(request, execution),
            selection=selection,
            context_ref=snapshot.context_ref,
            invocation_ref=InvocationReference(self._id("invocation")),
            limits=self._effective_limits(execution),
            idempotency_key=self._id("provider-key"),
        )

    @staticmethod
    def _usage_change(usage):
        return ExecutionRelatedChange(
            kind="provider-usage-recorded",
            duration_seconds=usage.duration_seconds,
            iterations=usage.iterations,
            provider_tokens=usage.provider_tokens,
            cost=usage.cost,
        )

    @staticmethod
    def _context_change(manifest_ref):
        return ExecutionRelatedChange(
            kind="context-manifest-recorded",
            reference=manifest_ref,
        )

    @classmethod
    def _changes_with_manifest(cls, usage, manifest_ref):
        return (cls._usage_change(usage), cls._context_change(manifest_ref))

    @staticmethod
    def _provider_delta(usage: RuntimeUsage) -> RuntimeUsage:
        return replace(usage, iterations=max(1, usage.iterations))

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid4()}"


__all__ = ["RuntimeService"]
