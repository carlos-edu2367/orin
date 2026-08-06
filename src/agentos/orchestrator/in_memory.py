from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256

from agentos.events.models import CommitState, DataClassification, EventEnvelope
from agentos.execution.models import ExecutionId, ExecutionState

from .models import (
    DispatchRequest,
    MaterializationStatus,
    OrchestrationPlan,
    OrchestrationReceipt,
    PlanId,
    PlanStatus,
    PlannedWork,
    ScheduleTrigger,
    SupervisionSnapshot,
)
from .ports import (
    DispatchReceipt,
    MaterializationRecord,
    PlanAccessContext,
    PlanStoreResult,
    ScheduleReceipt,
    SupervisionQuery,
)
from .security import OrchestratorAccessDenied, OrchestratorIdempotencyConflict, OrchestratorValidationError, OrchestratorVersionConflict, require_owner


class InMemoryPlanStore:
    def __init__(self, *, clock=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._plans: dict[str, OrchestrationPlan] = {}
        self._attempts: dict[tuple[str, int, str], MaterializationRecord] = {}
        self._idempotency: dict[tuple[tuple[str, str | None, str], str], tuple[str, OrchestrationReceipt]] = {}
        self._receipts: dict[tuple[tuple[str, str | None, str], str, str], OrchestrationReceipt] = {}
        self._unknown: dict[tuple[tuple[str, str | None, str], str], OrchestrationReceipt] = {}
        self._events: list[EventEnvelope] = []
        self._next_state: CommitState | None = None

    def submit(self, plan, *, access, idempotency_key, operation_fingerprint):
        self._authorize(plan, access)
        key = (access.scope_key(), idempotency_key)
        if key in self._unknown:
            raise OrchestratorValidationError("commit inspection required")
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != operation_fingerprint:
                raise OrchestratorIdempotencyConflict("idempotency key conflict")
            return PlanStoreResult(prior[1], self._plans.get(str(plan.plan_id)), True)
        receipt = self._new_receipt(plan, idempotency_key, CommitState.COMMITTED)
        state = self._consume_state()
        if state is not CommitState.COMMITTED:
            receipt = replace(receipt, commit_state=state)
            if state is CommitState.NOT_COMMITTED:
                self._receipts[(access.scope_key(), receipt.transaction_id, idempotency_key)] = receipt
            else:
                self._unknown[key] = receipt
            return PlanStoreResult(receipt)
        self._plans[str(plan.plan_id)] = plan
        self._idempotency[key] = (operation_fingerprint, receipt)
        self._receipts[(access.scope_key(), receipt.transaction_id, idempotency_key)] = receipt
        self._events.append(self._event("OrchestrationSubmitted", plan, {"plan_version": plan.version}))
        self._events.append(self._event("PlanVersionCreated", plan, {"plan_version": plan.version}))
        return PlanStoreResult(receipt, plan)

    def get(self, plan_id, access):
        plan = self._plans.get(str(plan_id))
        if plan is None:
            raise KeyError("plan not found")
        self._authorize(plan, access)
        return plan

    def list(self, access):
        return tuple(plan for plan in self._plans.values() if self._authorized(plan, access))

    def lookup_idempotency(self, access, idempotency_key):
        return self._idempotency.get((access.scope_key(), idempotency_key))

    def inspect_commit(self, *, access, transaction_id, idempotency_key):
        key = (access.scope_key(), idempotency_key)
        pending = self._unknown.pop(key, None)
        if pending is not None:
            result = replace(pending, commit_state=CommitState.NOT_COMMITTED, status="NOT_COMMITTED")
            self._receipts[(access.scope_key(), transaction_id, idempotency_key)] = result
            return result
        return self._receipts.get(
            (access.scope_key(), transaction_id, idempotency_key),
            OrchestrationReceipt("inspection:none", 1, transaction_id, idempotency_key, CommitState.NOT_COMMITTED, "NOT_COMMITTED"),
        )

    def materialize(self, *, plan_id, plan_version, work, execution_id, state_version, access, idempotency_key, retry_of=None):
        plan = self.get(plan_id, access)
        if int(plan.version) != plan_version:
            raise OrchestratorVersionConflict("plan version conflict")
        key = (str(plan_id), plan_version, str(work.work_id))
        existing = self._attempts.get(key) if retry_of is None else None
        if existing is not None:
            if existing.idempotency_key != idempotency_key:
                raise OrchestratorIdempotencyConflict("work materialization conflict")
            receipt = self._new_receipt(plan, idempotency_key, CommitState.COMMITTED, status="ALREADY_APPLIED")
            return PlanStoreResult(receipt, plan, True)
        receipt = self._new_receipt(plan, idempotency_key, CommitState.COMMITTED, status="MATERIALIZED")
        state = self._consume_state()
        if state is not CommitState.COMMITTED:
            return PlanStoreResult(replace(receipt, commit_state=state))
        storage_key = key if retry_of is None else (*key, str(execution_id))
        self._attempts[storage_key] = MaterializationRecord(plan_id, plan_version, work.work_id, execution_id, state_version, idempotency_key, retry_of)
        self._events.append(self._event("WorkMaterialized", plan, {"plan_version": plan_version, "work_id": str(work.work_id), "execution_id": str(execution_id)}))
        return PlanStoreResult(receipt, plan)

    def materialization(self, *, plan_id, plan_version, work_id, access):
        plan = self.get(plan_id, access)
        if int(plan.version) != plan_version:
            raise OrchestratorVersionConflict("plan version conflict")
        return self._attempts.get((str(plan_id), plan_version, str(work_id)))

    def materializations(self, *, plan_id, plan_version, access):
        self.get(plan_id, access)
        return tuple(record for key, record in self._attempts.items() if key[0] == str(plan_id) and key[1] == plan_version)

    def mark_expired(self, *, plan_id, plan_version, work, access, idempotency_key):
        plan = self.get(plan_id, access)
        receipt = self._new_receipt(plan, idempotency_key, CommitState.COMMITTED, status="EXPIRED")
        state = self._consume_state()
        if state is CommitState.COMMITTED:
            self._attempts[(str(plan_id), plan_version, str(work.work_id))] = MaterializationRecord(plan_id, plan_version, work.work_id, ExecutionId(""), 1, idempotency_key)
            self._events.append(self._event("OrchestrationExpired", plan, {"plan_version": plan_version, "work_id": str(work.work_id)}))
            return PlanStoreResult(receipt, plan)
        return PlanStoreResult(replace(receipt, commit_state=state))

    def cancel(self, *, plan_id, expected_version, access, idempotency_key, cancelled_execution_ids):
        plan = self.get(plan_id, access)
        if int(plan.version) != expected_version:
            raise OrchestratorVersionConflict("plan version conflict")
        receipt = self._new_receipt(plan, idempotency_key, CommitState.COMMITTED, status="CANCEL_REQUESTED")
        state = self._consume_state()
        if state is CommitState.COMMITTED:
            self._plans[str(plan_id)] = replace(plan, status=PlanStatus.CANCEL_REQUESTED)
            self._events.append(self._event("OrchestrationCancelled", plan, {"plan_version": expected_version, "execution_count": len(cancelled_execution_ids)}))
            return PlanStoreResult(receipt, self._plans[str(plan_id)])
        return PlanStoreResult(replace(receipt, commit_state=state))

    def events(self):
        return tuple(self._events)

    def unknown_next(self):
        self._next_state = CommitState.UNKNOWN

    def not_committed_next(self):
        self._next_state = CommitState.NOT_COMMITTED

    def _consume_state(self):
        state = self._next_state or CommitState.COMMITTED
        self._next_state = None
        return state

    def _new_receipt(self, plan, key, state, *, status="ACCEPTED"):
        token = sha256(f"{plan.plan_id}|{plan.version}|{key}|{status}".encode()).hexdigest()[:32]
        return OrchestrationReceipt(str(plan.plan_id), int(plan.version), f"tx:{token}", key, state, status)

    @staticmethod
    def _authorized(plan, access):
        return plan.user_id == access.user_id and plan.workspace_id == access.workspace_id and plan.actor == access.actor

    def _authorize(self, plan, access):
        if not self._authorized(plan, access):
            raise OrchestratorAccessDenied("orchestration access denied")

    def _event(self, event_type, plan, payload):
        return EventEnvelope(
            event_id=f"event:{plan.plan_id}:{len(self._events) + 1}",
            event_type=event_type,
            event_version=1,
            occurred_at=self._clock(),
            source="orchestrator",
            correlation_id=str(plan.correlation_id),
            sequence=None,
            user_id=str(plan.user_id),
            workspace_id=str(plan.workspace_id) if plan.workspace_id is not None else None,
            execution_id=None,
            agent_id=str(plan.nodes[0].agent_id) if plan.nodes else None,
            classification=DataClassification(plan.classification),
            causation_id=f"plan:{plan.plan_id}:v{plan.version}",
            payload={"purpose": str(plan.purpose), **payload},
        )


class InMemoryDispatch:
    def __init__(self) -> None:
        self._dispatches: dict[str, DispatchRequest] = {}

    @property
    def dispatches(self):
        return tuple(self._dispatches.values())

    def request_dispatch(self, request):
        prior = self._dispatches.get(request.idempotency_key)
        if prior is not None:
            return DispatchReceipt(prior.execution_id, True, True)
        self._dispatches[request.idempotency_key] = request
        return DispatchReceipt(request.execution_id, True)


class InMemoryScheduling:
    def __init__(self) -> None:
        self._triggers: dict[str, ScheduleTrigger] = {}

    @property
    def triggers(self):
        return tuple(self._triggers.values())

    def register(self, trigger):
        prior = self._triggers.get(str(trigger.trigger_id))
        if prior is not None:
            return ScheduleReceipt(str(trigger.trigger_id), True, True)
        self._triggers[str(trigger.trigger_id)] = trigger
        return ScheduleReceipt(str(trigger.trigger_id), True)

    def cancel(self, trigger_id, access):
        existing = self._triggers.get(str(trigger_id))
        if existing is not None and existing.user_id is not None and (
            existing.user_id != access.user_id or existing.workspace_id != access.workspace_id or existing.actor != access.actor
        ):
            raise OrchestratorAccessDenied("orchestration access denied")
        self._triggers.pop(str(trigger_id), None)
        return True


class InMemorySupervision:
    def __init__(self) -> None:
        self._snapshots: dict[str, SupervisionSnapshot] = {}
        self._owners: dict[str, PlanAccessContext] = {}
        self._observed: list[SupervisionSnapshot] = []

    @property
    def observed(self):
        return tuple(self._observed)

    def set(self, snapshot, access=None):
        self._snapshots[str(snapshot.execution_id)] = snapshot
        if access is not None:
            self._owners[str(snapshot.execution_id)] = access

    def observe(self, query_or_execution_id, access=None):
        if isinstance(query_or_execution_id, SupervisionQuery):
            execution_id, access = query_or_execution_id.execution_id, query_or_execution_id.access
        else:
            execution_id = query_or_execution_id
        snapshot = self._snapshots.get(str(execution_id))
        if snapshot is None:
            raise KeyError("execution not found")
        owner = self._owners.get(str(execution_id))
        if owner is not None and owner.scope_key() != access.scope_key():
            raise OrchestratorAccessDenied("orchestration access denied")
        self._observed.append(snapshot)
        return snapshot


__all__ = ["InMemoryDispatch", "InMemoryPlanStore", "InMemoryScheduling", "InMemorySupervision"]
