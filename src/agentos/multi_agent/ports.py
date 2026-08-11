from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agentos.agents import AdministrativeExecutionRef, AgentAccessContext, AgentResolutionRequest, ResolvedAgent
from agentos.context import HandoffRef, StructuredHandoff
from agentos.events import EventEnvelope
from agentos.execution.ports import ExecutionCommandContext
from agentos.execution.models import ExecutionLimits

from .models import (
    AgentMessage,
    CancelDelegation,
    Collaboration,
    Delegation,
    DelegationResult,
    DelegateTask,
    MessageExecutionRef,
    ReturnDelegationResult,
    SendAgentMessage,
    WaitForDelegations,
    WaitReceipt,
)


class CommitState(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    transaction_id: str
    idempotency_key: str
    commit_state: CommitState


class AgentResolverPort(Protocol):
    def resolve(self, request: AgentResolutionRequest) -> ResolvedAgent: ...


class AgentAdministrationPort(Protocol):
    def request_create(self, command) -> AdministrativeExecutionRef: ...


class ExecutionLifecyclePort(Protocol):
    def create_delivery(self, *, request: SendAgentMessage, execution_id: str) -> object: ...

    def create_child(self, *, request: DelegateTask, execution_id: str, resolved_agent: ResolvedAgent) -> object: ...

    def request_pause(self, context: ExecutionCommandContext, *, expected_version: int, idempotency_key: str) -> object: ...

    def request_resume(self, context: ExecutionCommandContext, *, expected_version: int, idempotency_key: str) -> object: ...

    def request_cancel(self, context: ExecutionCommandContext, *, expected_version: int, idempotency_key: str) -> object: ...


class ContextSharingPort(Protocol):
    def resolve_handoff(self, ref: HandoffRef, *, user_id: str, workspace_id: str | None, purpose: str, now) -> StructuredHandoff: ...


class ChildModelPolicyPort(Protocol):
    def validate(self, *, parent: ResolvedAgent, child: ResolvedAgent, command: DelegateTask) -> None: ...


@runtime_checkable
class MultiAgentEventRecorder(Protocol):
    def record_event(self, event: EventEnvelope) -> bool: ...


class CollaborationStore(Protocol):
    def save_collaboration(self, collaboration: Collaboration, *, idempotency_key: str) -> Collaboration: ...

    def get_collaboration(self, collaboration_id: str, user_id: str, workspace_id: str | None) -> Collaboration: ...

    def save_message(self, message: AgentMessage, *, fingerprint: str) -> AgentMessage: ...

    def save_delegation(self, delegation: Delegation, *, fingerprint: str) -> Delegation: ...

    def save_result(self, result: DelegationResult, *, fingerprint: str) -> DelegationResult: ...

    def save_wait(self, command: WaitForDelegations, checkpoint_ref: str, *, fingerprint: str) -> WaitReceipt: ...


class MultiAgentCoordinator(Protocol):
    def request_agent_creation(self, command): ...

    def send(self, command: SendAgentMessage) -> MessageExecutionRef: ...

    def delegate(self, command: DelegateTask): ...

    def wait_for(self, command: WaitForDelegations) -> WaitReceipt: ...

    def return_result(self, command: ReturnDelegationResult): ...

    def request_cancel(self, command: CancelDelegation): ...


__all__ = [
    "AgentAdministrationPort", "AgentResolverPort", "CollaborationStore", "CommitReceipt",
    "ChildModelPolicyPort", "CommitState", "ContextSharingPort", "ExecutionLifecyclePort", "MultiAgentCoordinator",
    "MultiAgentEventRecorder",
]
