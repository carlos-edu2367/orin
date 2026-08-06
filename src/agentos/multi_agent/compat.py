from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

from agentos.agents.ports import AgentResolutionRequest
from agentos.execution.models import CancellationReason, CancellationReasonCode, Execution, Ownership, TaskSnapshot
from agentos.execution.ports import (
    CancelExecution,
    CreateExecution,
    ExecutionCommandContext,
    ExecutionControl,
    PauseExecution,
    ResumeExecution,
)

from .models import DelegateTask, SendAgentMessage


class MultiAgentExecutionRejected(ValueError):
    pass


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

    def request_create(self, command):
        return self._administration.request_create(command)


class ExecutionControlAdapter:
    """Translate multi-agent intentions to the public ExecutionControl facade."""

    def __init__(self, control: ExecutionControl) -> None:
        self._control = control

    def create_delivery(self, *, request: SendAgentMessage, execution_id: str):
        execution = Execution.create(
            execution_id=execution_id,
            ownership=Ownership(request.user_id, request.workspace_id),
            agent_id=request.recipient_agent_id,
            task=TaskSnapshot(f"message:{request.kind.value.lower()}", 1),
            correlation_id=request.correlation_id,
            limits=_delivery_limits(),
            now=request.requested_at,
            causation_id=request.causation_id,
        )
        context = self._context(request.user_id, request.workspace_id, request.recipient_agent_id, execution_id, request.correlation_id, request.purpose)
        command = CreateExecution(
            context=context,
            command_id=f"command:message:{request.idempotency_key}",
            idempotency_key=request.idempotency_key,
            expected_version=None,
            requested_at=request.requested_at,
            execution=execution,
        )
        result = self._control.create(command)
        if not hasattr(result, "resulting_version"):
            raise MultiAgentExecutionRejected("delivery execution rejected")
        return ExecutionCreationReceipt(execution_id, result.resulting_version, getattr(result, "transaction_id", None))

    def create_child(self, *, request: DelegateTask, execution_id: str, resolved_agent):
        execution = Execution.create(
            execution_id=execution_id,
            ownership=Ownership(request.user_id, request.workspace_id),
            agent_id=request.delegate_agent_id,
            task=TaskSnapshot(request.handoff_ref.handoff_id, request.handoff_ref.version),
            correlation_id=request.correlation_id,
            limits=request.child_limits,
            now=request.requested_at,
            causation_id=request.causation_id,
            parent_execution_id=request.parent_execution_id,
            agent_config_version=resolved_agent.config_version,
        )
        context = self._context(request.user_id, request.workspace_id, request.delegate_agent_id, execution_id, request.correlation_id, request.purpose)
        command = CreateExecution(
            context=context,
            command_id=f"command:delegation:{request.idempotency_key}",
            idempotency_key=request.idempotency_key,
            expected_version=None,
            requested_at=request.requested_at,
            execution=execution,
        )
        result = self._control.create(command)
        if not hasattr(result, "resulting_version"):
            raise MultiAgentExecutionRejected("child execution rejected")
        return ExecutionCreationReceipt(execution_id, result.resulting_version, getattr(result, "transaction_id", None))

    def request_pause(self, context: ExecutionCommandContext, *, expected_version: int, idempotency_key: str):
        return self._control.request_pause(
            PauseExecution(
                context=context,
                command_id=f"command:pause:{idempotency_key}",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                requested_at=_now_from_context(context),
            )
        )

    def request_resume(self, context: ExecutionCommandContext, *, expected_version: int, idempotency_key: str):
        return self._control.request_resume(
            ResumeExecution(
                context=context,
                command_id=f"command:resume:{idempotency_key}",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                requested_at=_now_from_context(context),
            )
        )

    def request_cancel(self, context: ExecutionCommandContext, *, expected_version: int, idempotency_key: str):
        return self._control.request_cancel(
            CancelExecution(
                context=context,
                command_id=f"command:cancel:{idempotency_key}",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                requested_at=_now_from_context(context),
                reason=CancellationReason(CancellationReasonCode.POLICY_REQUESTED),
            )
        )

    @staticmethod
    def _context(user_id, workspace_id, agent_id, execution_id, correlation_id, purpose):
        return ExecutionCommandContext(
            user_id=user_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            purpose=purpose,
        )


@dataclass(frozen=True, slots=True)
class ExecutionCreationReceipt:
    execution_id: str
    state_version: int
    transaction_id: str | None


def _delivery_limits():
    from agentos.execution.models import ExecutionLimits

    return ExecutionLimits(max_duration_seconds=60, max_iterations=1)


def _now_from_context(context):
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


__all__ = [
    "AgentAdministrationAdapter", "AgentResolverAdapter", "ExecutionControlAdapter",
    "ExecutionCreationReceipt", "MultiAgentExecutionRejected",
]
