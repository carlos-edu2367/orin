from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from agentos.execution.models import ExecutionState, Ownership
from agentos.execution.ports import Accepted, AlreadyApplied
from agentos.events.models import CommitState

from .compat import ExecutionCancellationAdapter
from .models import (
    AdministerAgent,
    CancellationReceipt,
    ContinueExecution,
    CreateExecutionRequest,
    DependencyCondition,
    DependencyFailurePolicy,
    DispatchRequest,
    EvaluationOutcome,
    EvaluationTrigger,
    ExecutePlan,
    ExecutionCreationReceipt,
    OrchestrationPlan,
    OrchestrationPlanDraft,
    OrchestrationPolicy,
    OrchestrationReceipt,
    OrchestrationRequest,
    PlanStatus,
    PlannedWork,
    ProcessingClass,
    RetryExecution,
    RetryReceipt,
    RunAgentTask,
    ScheduleTrigger,
)
from .ports import PlanAccessContext, PlanStorePort, SupervisionQuery
from .security import OrchestratorAccessDenied, OrchestratorValidationError, fingerprint, require_owner, validate_plan


_TERMINAL = {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED}


class OrchestratorService:
    def __init__(self, *, plan_store: PlanStorePort, execution_factory, dispatch, scheduling, resolver, supervision, cancellation=None, administration=None, continuation=None, clock=None, plan_id_factory=None) -> None:
        self._store = plan_store
        self._execution_factory = execution_factory
        self._dispatch = dispatch
        self._scheduling = scheduling
        self._resolver = resolver
        self._supervision = supervision
        self._cancellation = cancellation
        self._administration = administration
        self._continuation = continuation
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._plan_id_factory = plan_id_factory

    def submit(self, request: OrchestrationRequest):
        access = self._access(request.actor, request.user_id, request.workspace_id, request.purpose, request.correlation_id, request.classification)
        if isinstance(request.intent, AdministerAgent):
            if self._administration is None:
                raise OrchestratorValidationError("administrative operation unavailable")
            operation = getattr(request.intent.operation, "operation", "create")
            return self._administration.request(request.intent.operation, operation=operation)
        if isinstance(request.intent, ContinueExecution):
            if self._continuation is None:
                raise OrchestratorValidationError("continuation is unavailable")
            return self._continuation.continue_execution(request.intent, access)
        draft = self._draft(request)
        validate_plan(draft)
        plan_id = self._plan_id(request.idempotency_key)
        plan = OrchestrationPlan.from_draft(plan_id, draft)
        result = self._store.submit(plan, access=access, idempotency_key=request.idempotency_key, operation_fingerprint=fingerprint(request))
        for node in draft.nodes:
            if node.schedule is not None:
                self._scheduling.register(self._trigger(plan, node, request.idempotency_key))
        return result.receipt

    def evaluate(self, plan_id, trigger: EvaluationTrigger) -> EvaluationOutcome:
        access = self._trigger_access(trigger)
        plan = self._store.get(plan_id, access)
        now = self._clock()
        if plan.status is not PlanStatus.ACTIVE:
            return EvaluationOutcome(plan.plan_id, plan.version)
        ready: list[str] = []
        materialized: list[str] = []
        expired: list[str] = []
        dispatches: list[DispatchRequest] = []
        active_count = sum(1 for item in self._store.materializations(plan_id=plan.plan_id, plan_version=plan.version, access=access) if str(item.execution_id))
        for work in plan.nodes:
            if self._store.materialization(plan_id=plan.plan_id, plan_version=plan.version, work_id=work.work_id, access=access) is not None:
                continue
            if work.schedule is not None and now < work.schedule.not_before:
                continue
            if (work.deadline_at is not None and now > work.deadline_at) or (work.schedule is not None and work.schedule.expires_at is not None and now > work.schedule.expires_at):
                result = self._store.mark_expired(plan_id=plan.plan_id, plan_version=plan.version, work=work, access=access, idempotency_key=f"expire:{plan.plan_id}:{plan.version}:{work.work_id}")
                if result.receipt.commit_state.value == "COMMITTED":
                    expired.append(str(work.work_id))
                continue
            if active_count >= plan.policy.maximum_parallel_executions:
                continue
            if not self._dependencies_ready(plan, work, access):
                continue
            ready.append(str(work.work_id))
            resolved = self._resolver.resolve(
                agent_id=str(work.agent_id), user_id=str(plan.user_id), workspace_id=plan.workspace_id,
                purpose=str(work.purpose), correlation_id=str(plan.correlation_id), actor=plan.actor, classification=work.classification,
            )
            creation = self._execution_factory.create(
                CreateExecutionRequest(
                    ownership=Ownership(str(plan.user_id), plan.workspace_id),
                    agent_id=work.agent_id,
                    agent_config_version=int(resolved.config_version),
                    task=work.task,
                    limits=work.limits,
                    correlation_id=plan.correlation_id,
                    purpose=work.purpose,
                    idempotency_key=f"materialize:{plan.plan_id}:{plan.version}:{work.work_id}",
                    requested_at=now,
                    causation_id=str(trigger.cause_ref) if trigger.cause_ref else None,
                )
            )
            if creation.commit_state.value != "COMMITTED":
                continue
            stored = self._store.materialize(
                plan_id=plan.plan_id, plan_version=plan.version, work=work, execution_id=creation.execution_id,
                state_version=creation.state_version, access=access, idempotency_key=f"materialize:{plan.plan_id}:{plan.version}:{work.work_id}",
            )
            if stored.receipt.commit_state.value != "COMMITTED":
                continue
            materialized.append(str(creation.execution_id))
            active_count += 1
            dispatch_request = DispatchRequest(
                execution_id=creation.execution_id, expected_state_version=creation.state_version,
                processing_class=ProcessingClass.STANDARD, correlation_id=plan.correlation_id,
                purpose=work.purpose, idempotency_key=f"dispatch:{creation.execution_id}:{creation.state_version}",
            )
            self._dispatch.request_dispatch(dispatch_request)
            dispatches.append(dispatch_request)
        return EvaluationOutcome(plan.plan_id, plan.version, tuple(ready), tuple(materialized), tuple(expired), tuple(dispatches), CommitState.COMMITTED)

    def request_cancel(self, command) -> CancellationReceipt:
        access = self._access(command.actor, command.user_id, command.workspace_id, command.purpose, command.correlation_id, "INTERNAL")
        plan = self._store.get(command.plan_id, access)
        records = self._store.materializations(plan_id=plan.plan_id, plan_version=plan.version, access=access)
        cancelled: list[str] = []
        for record in records:
            if not str(record.execution_id):
                continue
            try:
                snapshot = self._supervision.observe(record.execution_id, access)
            except (KeyError, LookupError):
                continue
            if snapshot.observed_state in _TERMINAL or self._cancellation is None:
                continue
            result = self._cancellation.cancel(
                execution_id=str(record.execution_id), ownership=Ownership(str(plan.user_id), plan.workspace_id),
                agent_id=str(next(node.agent_id for node in plan.nodes if str(node.work_id) == str(record.work_id))),
                correlation_id=str(plan.correlation_id), purpose=str(plan.purpose), actor=command.actor,
                idempotency_key=f"{command.idempotency_key}:{record.execution_id}", expected_version=int(snapshot.state_version), requested_at=command.requested_at,
            )
            if isinstance(result, (Accepted, AlreadyApplied)):
                cancelled.append(str(record.execution_id))
        stored = self._store.cancel(plan_id=plan.plan_id, expected_version=command.expected_version or plan.version, access=access, idempotency_key=command.idempotency_key, cancelled_execution_ids=tuple(cancelled))
        return CancellationReceipt(plan.plan_id, stored.receipt.plan_version, tuple(cancelled), stored.receipt.commit_state, stored.receipt.transaction_id)

    def request_retry(self, command: RetryExecution) -> RetryReceipt:
        access = self._access(command.actor, command.user_id, command.workspace_id, command.purpose, command.correlation_id, "INTERNAL")
        plan = self._store.get(command.plan_id, access)
        if int(plan.version) != command.expected_plan_version:
            raise OrchestratorValidationError("plan version conflict")
        work = next((node for node in plan.nodes if str(node.work_id) == str(command.work_id)), None)
        prior = self._store.materialization(plan_id=plan.plan_id, plan_version=plan.version, work_id=command.work_id, access=access)
        if work is None or prior is None or str(prior.execution_id) != str(command.previous_execution_id):
            raise OrchestratorValidationError("retry target is invalid")
        snapshot = self._supervision.observe(prior.execution_id, access)
        if snapshot.observed_state not in _TERMINAL:
            raise OrchestratorValidationError("retry requires a terminal attempt")
        resolved = self._resolver.resolve(agent_id=str(work.agent_id), user_id=str(plan.user_id), workspace_id=plan.workspace_id, purpose=str(work.purpose), correlation_id=str(plan.correlation_id), actor=plan.actor, classification=work.classification)
        creation = self._execution_factory.create(CreateExecutionRequest(
            ownership=Ownership(str(plan.user_id), plan.workspace_id), agent_id=work.agent_id, agent_config_version=int(resolved.config_version),
            task=work.task, limits=work.limits, correlation_id=plan.correlation_id, purpose=work.purpose, idempotency_key=command.idempotency_key,
            requested_at=command.requested_at, causation_id=str(command.previous_execution_id),
        ))
        if creation.commit_state.value != "COMMITTED":
            return RetryReceipt(plan.plan_id, work.work_id, command.previous_execution_id, creation.execution_id, creation.commit_state, creation.transaction_id)
        stored = self._store.materialize(plan_id=plan.plan_id, plan_version=plan.version, work=work, execution_id=creation.execution_id, state_version=creation.state_version, access=access, idempotency_key=command.idempotency_key, retry_of=command.previous_execution_id)
        if stored.receipt.commit_state.value == "COMMITTED":
            dispatch = DispatchRequest(creation.execution_id, creation.state_version, ProcessingClass.STANDARD, plan.correlation_id, work.purpose, f"dispatch:{creation.execution_id}:{creation.state_version}")
            self._dispatch.request_dispatch(dispatch)
        return RetryReceipt(plan.plan_id, work.work_id, command.previous_execution_id, creation.execution_id, stored.receipt.commit_state, stored.receipt.transaction_id)

    def _draft(self, request):
        intent = request.intent
        if isinstance(intent, RunAgentTask):
            node = PlannedWork(
                work_id=f"work:{self._short(request.idempotency_key)}", agent_id=intent.agent_id, task=intent.task,
                limits=intent.limits, idempotency_key=f"{request.idempotency_key}:work", purpose=request.purpose,
                classification=request.classification,
            )
            return OrchestrationPlanDraft(request.user_id, request.workspace_id, request.actor, request.correlation_id, request.purpose, request.classification, (node,), (), OrchestrationPolicy(), request.requested_at)
        if isinstance(intent, ExecutePlan):
            draft = intent.plan
            require_owner(expected_user_id=str(request.user_id), expected_workspace_id=request.workspace_id, actual_user_id=str(draft.user_id), actual_workspace_id=draft.workspace_id)
            if draft.actor != request.actor or draft.purpose != request.purpose or draft.correlation_id != request.correlation_id:
                raise OrchestratorAccessDenied("orchestration access denied")
            return draft
        raise OrchestratorValidationError("orchestration intent rejected")

    def _dependencies_ready(self, plan, work, access):
        incoming = [edge for edge in plan.dependencies if str(edge.successor_work_id) == str(work.work_id)]
        for edge in incoming:
            record = self._store.materialization(plan_id=plan.plan_id, plan_version=plan.version, work_id=edge.predecessor_work_id, access=access)
            if record is None or not str(record.execution_id):
                return False
            try:
                snapshot = self._supervision.observe(record.execution_id, access)
            except (KeyError, LookupError):
                return False
            if edge.condition is DependencyCondition.COMPLETED and snapshot.observed_state is not ExecutionState.COMPLETED:
                return False
            if edge.condition is DependencyCondition.TERMINAL and snapshot.observed_state not in _TERMINAL:
                return False
            if edge.condition is DependencyCondition.RESULT_MATCHED and (snapshot.observed_state is not ExecutionState.COMPLETED or edge.result_ref is None or snapshot.result_ref != edge.result_ref):
                return False
            if snapshot.observed_state is ExecutionState.FAILED and edge.failure_policy is DependencyFailurePolicy.DO_NOT_MATERIALIZE:
                return False
        return True

    def _trigger_access(self, trigger):
        if None in (trigger.actor, trigger.user_id, trigger.purpose, trigger.correlation_id):
            raise OrchestratorAccessDenied("orchestration access denied")
        return self._access(trigger.actor, trigger.user_id, trigger.workspace_id, trigger.purpose, trigger.correlation_id, "INTERNAL")

    @staticmethod
    def _access(actor, user_id, workspace_id, purpose, correlation_id, classification):
        return PlanAccessContext(str(user_id), workspace_id, str(actor), str(purpose), str(correlation_id), classification)

    def _plan_id(self, key):
        return self._plan_id_factory() if self._plan_id_factory is not None else f"plan:{self._short(key)}"

    @staticmethod
    def _short(value):
        return sha256(str(value).encode()).hexdigest()[:24]

    @staticmethod
    def _trigger(plan, node, key):
        return ScheduleTrigger(f"trigger:{plan.plan_id}:{node.work_id}", plan.plan_id, node.work_id, node.schedule, f"{key}:{node.work_id}")


__all__ = ["OrchestratorService"]
