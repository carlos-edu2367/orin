from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from agentos.agents.ports import AgentResolutionRequest
from agentos.execution.models import CancellationReason, CancellationReasonCode, Execution, Ownership
from agentos.execution.ports import (
    Accepted,
    AlreadyApplied,
    CancelExecution,
    CreateExecution,
    ExecutionCommandContext,
    ExecutionControl,
    Indeterminate,
)

from .models import CreateExecutionRequest, ExecutionCreationReceipt
from .ports import PlanAccessContext
from .security import fingerprint


class ExecutionFactoryRejected(ValueError):
    """A sanitized failure while the Kernel rejected creation."""


class ExecutionControlExecutionFactory:
    """Translate one orchestration attempt to the public Kernel facade."""

    def __init__(self, control: ExecutionControl, *, execution_id_factory=None) -> None:
        self._control = control
        self._execution_id_factory = execution_id_factory or (lambda: f"execution:{uuid4().hex}")
        self._idempotency: dict[str, tuple[str, ExecutionCreationReceipt]] = {}

    def create(self, request: CreateExecutionRequest) -> ExecutionCreationReceipt:
        operation_fingerprint = fingerprint(request)
        prior = self._idempotency.get(request.idempotency_key)
        if prior is not None:
            if prior[0] != operation_fingerprint:
                raise ExecutionFactoryRejected("execution idempotency conflict")
            return prior[1]
        execution_id = self._execution_id_factory()
        execution = Execution.create(
            execution_id=execution_id,
            ownership=request.ownership,
            agent_id=request.agent_id,
            task=request.task,
            correlation_id=request.correlation_id,
            limits=request.limits,
            now=request.requested_at,
            causation_id=request.causation_id,
            parent_execution_id=request.parent_execution_id,
            agent_config_version=request.agent_config_version,
        )
        context = ExecutionCommandContext(
            user_id=request.ownership.user_id,
            workspace_id=request.ownership.workspace_id,
            agent_id=str(request.agent_id),
            execution_id=execution_id,
            correlation_id=request.correlation_id,
            purpose=request.purpose,
        )
        command = CreateExecution(
            context=context,
            command_id=f"command:{uuid4().hex}",
            idempotency_key=request.idempotency_key,
            expected_version=None,
            requested_at=request.requested_at,
            execution=execution,
        )
        result = self._control.create(command)
        if isinstance(result, (Accepted, AlreadyApplied)):
            receipt = ExecutionCreationReceipt(
                execution_id=execution_id,
                state_version=result.resulting_version,
                transaction_id=result.transaction_id,
                commit_state="COMMITTED",
                already_applied=isinstance(result, AlreadyApplied),
            )
            self._idempotency[request.idempotency_key] = (operation_fingerprint, receipt)
            return receipt
        if isinstance(result, Indeterminate):
            return ExecutionCreationReceipt(execution_id, 1, result.transaction_id, "UNKNOWN")
        raise ExecutionFactoryRejected("execution creation rejected")


class ExecutionCancellationAdapter:
    def __init__(self, control: ExecutionControl) -> None:
        self._control = control

    def cancel(
        self,
        *,
        execution_id: str,
        ownership: Ownership,
        agent_id: str,
        correlation_id: str,
        purpose: str,
        actor: str,
        idempotency_key: str,
        expected_version: int,
        requested_at,
        reason: CancellationReason | None = None,
    ):
        context = ExecutionCommandContext(
            user_id=ownership.user_id,
            workspace_id=ownership.workspace_id,
            agent_id=agent_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            purpose=purpose,
        )
        command = CancelExecution(
            context=context,
            command_id=f"command:{uuid4().hex}",
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            requested_at=requested_at,
            reason=reason or CancellationReason(CancellationReasonCode.USER_REQUESTED),
        )
        return self._control.request_cancel(command)


class AgentResolverAdapter:
    def __init__(self, registry) -> None:
        self._registry = registry

    def resolve(self, *, agent_id: str, user_id: str, workspace_id: str | None, purpose: str, correlation_id: str, actor: str, classification):
        return self._registry.resolve_for_execution(
            AgentResolutionRequest(
                agent_id=agent_id,
                user_id=user_id,
                workspace_id=workspace_id,
                requested_config_version=None,
                purpose=purpose,
                correlation_id=correlation_id,
                actor=actor,
                classification=classification,
            )
        )


class AgentAdministrationAdapter:
    def __init__(self, administration) -> None:
        self._administration = administration

    def request(self, command, *, operation: str):
        if operation not in {"create", "reconfigure", "suspend", "resume", "archive", "assign_workspace", "unassign_workspace"}:
            raise ValueError("administrative operation rejected")
        method = getattr(self._administration, f"request_{operation}", None)
        if method is None:
            raise ValueError("administrative operation unavailable")
        return method(command)


def plan_access_context(*, user_id: str, workspace_id: str | None, actor: str, purpose: str, correlation_id: str, classification) -> PlanAccessContext:
    return PlanAccessContext(user_id, workspace_id, actor, purpose, correlation_id, classification)


__all__ = [
    "AgentAdministrationAdapter", "AgentResolverAdapter", "ExecutionCancellationAdapter",
    "ExecutionControlExecutionFactory", "ExecutionFactoryRejected", "plan_access_context",
]
