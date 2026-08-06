from __future__ import annotations

from dataclasses import dataclass, replace

from agentos.execution.models import Execution
from agentos.context.models import ContextOperationContext
from agentos.providers.models import ProviderOperationContext
from agentos.events.models import CommitState, EventEnvelope
from agentos.events.ports import (
    ConfirmedOutboxSource,
    OutboxPosition,
    OutboxRecord,
    PublishOutboxBatch,
)

from .models import OpaqueAgentReference, ResolvedAgent


@dataclass(frozen=True, slots=True)
class AgentContextSeed:
    agent_id: str
    config_version: int
    prompt_ref: OpaqueAgentReference
    prompt_version: int
    private_memory_scope_ref: OpaqueAgentReference
    context_policy_ref: OpaqueAgentReference
    classification: str


@dataclass(frozen=True, slots=True)
class AgentProviderSeed:
    agent_id: str
    config_version: int
    model_profile_ref: OpaqueAgentReference
    execution_policy_ref: OpaqueAgentReference
    purpose: str
    classification: str


def to_context_seed(resolved: ResolvedAgent) -> AgentContextSeed:
    return AgentContextSeed(
        agent_id=str(resolved.agent_id),
        config_version=int(resolved.config_version),
        prompt_ref=resolved.prompt.prompt_ref,
        prompt_version=resolved.prompt.prompt_version,
        private_memory_scope_ref=resolved.private_memory_scope.scope_ref,
        context_policy_ref=resolved.policies.context_policy_ref,
        classification=resolved.policies.classification.value,
    )


def to_provider_seed(resolved: ResolvedAgent) -> AgentProviderSeed:
    return AgentProviderSeed(
        agent_id=str(resolved.agent_id),
        config_version=int(resolved.config_version),
        model_profile_ref=resolved.model_profile_ref,
        execution_policy_ref=resolved.policies.execution_policy_ref,
        purpose=resolved.policies.purpose,
        classification=resolved.policies.classification.value,
    )


def attach_config_version(
    execution: Execution,
    resolved: ResolvedAgent,
    *,
    correlation_id: str | None = None,
    purpose: str | None = None,
) -> Execution:
    if execution.agent_id != resolved.agent_id:
        raise ValueError("Agent does not match Execution")
    if execution.ownership.user_id != resolved.user_id or execution.ownership.workspace_id != resolved.workspace_id:
        raise ValueError("Agent ownership does not match Execution")
    if correlation_id is not None and correlation_id != execution.correlation_id:
        raise ValueError("correlation does not match Execution")
    if purpose is not None and purpose != resolved.policies.purpose:
        raise ValueError("purpose does not match resolved Agent")
    return replace(execution, agent_config_version=int(resolved.config_version))


def to_context_operation_context(
    resolved: ResolvedAgent, *, execution_id: str, correlation_id: str, purpose: str
) -> ContextOperationContext:
    if purpose != resolved.policies.purpose:
        raise ValueError("purpose does not match resolved Agent")
    return ContextOperationContext(
        user_id=resolved.user_id,
        workspace_id=resolved.workspace_id,
        agent_id=resolved.agent_id,
        execution_id=execution_id,
        correlation_id=correlation_id,
        purpose=purpose,
    )


def to_provider_operation_context(
    resolved: ResolvedAgent,
    *,
    execution_id: str,
    correlation_id: str,
    purpose: str,
    actor_ref: str,
) -> ProviderOperationContext:
    if purpose != resolved.policies.purpose:
        raise ValueError("purpose does not match resolved Agent")
    return ProviderOperationContext(
        user_id=resolved.user_id,
        workspace_id=resolved.workspace_id,
        agent_id=resolved.agent_id,
        execution_id=execution_id,
        correlation_id=correlation_id,
        purpose=purpose,
        actor_ref=actor_ref,
    )


def agent_outbox_records(persistence) -> tuple[OutboxRecord, ...]:
    records = []
    for index, event in enumerate(persistence.confirmed_outbox()):
        receipt = next(
            receipt
            for receipt in persistence._receipts.values()
            if receipt.event_id == event.event_id
        )
        records.append(
            OutboxRecord(
                event=event,
                position=OutboxPosition(f"{index}"),
                commit_state=persistence._event_states.get(event.event_id, CommitState.COMMITTED),
                transaction_id=receipt.transaction_id,
            )
        )
    return tuple(records)


class InMemoryAgentOutboxSource(ConfirmedOutboxSource):
    """Read-only bridge from Agent's committed outbox to the canonical publisher."""

    def __init__(self, persistence) -> None:
        self._persistence = persistence

    def read_outbox(self, request: PublishOutboxBatch) -> tuple[OutboxRecord, ...]:
        after = -1 if request.after_position is None else int(request.after_position.value)
        records = tuple(record for record in agent_outbox_records(self._persistence) if int(record.position.value) > after)
        return records[: request.maximum_events]

    def inspect_commit(self, record: OutboxRecord, request: PublishOutboxBatch) -> bool:
        if request.context is None:
            return False
        return (
            record.event.user_id == request.context.user_id
            and record.event.workspace_id == request.context.workspace_id
            and record.event.agent_id == request.context.agent_id
            and record.event.execution_id == request.context.execution_id
            and record.event.correlation_id == request.context.correlation_id
            and record.event.event_id in {event.event_id for event in self._persistence.confirmed_outbox()}
        )


__all__ = [
    "AgentContextSeed",
    "AgentProviderSeed",
    "InMemoryAgentOutboxSource",
    "attach_config_version",
    "agent_outbox_records",
    "to_context_operation_context",
    "to_context_seed",
    "to_provider_operation_context",
    "to_provider_seed",
]
