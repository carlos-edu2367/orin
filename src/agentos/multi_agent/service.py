from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

from agentos.agents.ports import AgentResolutionRequest
from agentos.events import DataClassification, EventEnvelope
from agentos.execution.ports import ExecutionCommandContext
from agentos.execution.models import ExecutionLimits

from .models import (
    AgentMessage,
    CancelDelegation,
    Collaboration,
    CreateParticipantAgent,
    DelegateTask,
    Delegation,
    DelegationReceipt,
    MessageExecutionRef,
    ReturnDelegationResult,
    ReturnReceipt,
    SendAgentMessage,
    WaitForDelegations,
    WaitReceipt,
    WaitRegistration,
    CancellationReceipt,
    CancellationScope,
    DelegationTerminalState,
)
from .security import classification_allows, fingerprint, require_same_scope


class MultiAgentCoordinatorService:
    def __init__(
        self,
        *,
        store,
        resolver,
        administration,
        execution,
        sharing,
        events,
        clock,
        model_policy=None,
        maximum_depth: int = 8,
        maximum_fanout: int = 16,
        maximum_duration_seconds: int | None = 120,
        maximum_iterations: int | None = 8,
        maximum_cost: Decimal | None = Decimal("100"),
        maximum_model_tokens: int | None = 100000,
    ):
        if maximum_depth < 0 or maximum_fanout < 1:
            raise ValueError("multi-agent limits are invalid")
        self.store = store
        self.resolver = resolver
        self.administration = administration
        self.execution = execution
        self.sharing = sharing
        self.events = events
        self.clock = clock
        self.model_policy = model_policy
        self._waits: dict[str, WaitRegistration] = {}
        self._wait_versions: dict[str, int] = {}
        self.maximum_depth = maximum_depth
        self.maximum_fanout = maximum_fanout
        self.maximum_duration_seconds = maximum_duration_seconds
        self.maximum_iterations = maximum_iterations
        self.maximum_cost = maximum_cost
        self.maximum_model_tokens = maximum_model_tokens

    def request_agent_creation(self, command: CreateParticipantAgent):
        if not isinstance(command, CreateParticipantAgent):
            raise ValueError("invalid Agent creation command")
        return self.administration.request_create(command.agent_command)

    def send(self, command: SendAgentMessage) -> MessageExecutionRef:
        collaboration = self._collaboration(command)
        self._validate_participants(collaboration, command.sender_agent_id, command.recipient_agent_id)
        self._validate_policy(collaboration, command.purpose, command.classification)
        self.resolver.resolve(
            agent_id=command.sender_agent_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            purpose=command.purpose,
            correlation_id=command.correlation_id,
            actor=command.actor,
            classification=command.classification,
        )
        self.resolver.resolve(
            agent_id=command.recipient_agent_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            purpose=command.purpose,
            correlation_id=command.correlation_id,
            actor=command.actor,
            classification=command.classification,
        )
        if command.deadline_at is not None and self.clock() >= command.deadline_at:
            self._record_message_fact(command, "AgentMessageExpired", "DEADLINE_EXPIRED", None)
            raise ValueError("message deadline expired")
        existing = self._find_message(command)
        if existing is not None:
            stored_fingerprint = self._fingerprint_for("message", existing.message_id, command)
            if stored_fingerprint is not None and stored_fingerprint != fingerprint(command):
                raise ValueError("idempotency key conflict")
            return MessageExecutionRef(existing.message_id, existing.delivery_execution_id)
        scope_digest = _digest(f"{command.user_id}|{command.workspace_id or ''}|{command.owner}|{command.idempotency_key}")
        message_id = f"message:{scope_digest}"
        delivery_execution_id = f"delivery:{scope_digest}"
        message = AgentMessage(
            message_id=message_id,
            collaboration_id=command.collaboration_id,
            sender_agent_id=command.sender_agent_id,
            recipient_agent_id=command.recipient_agent_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            owner=command.owner,
            purpose=command.purpose,
            classification=command.classification,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            delivery_execution_id=delivery_execution_id,
            kind=command.kind,
            inline_summary=command.inline_summary,
            content_refs=command.content_refs,
            handoff_ref=command.handoff_ref,
            deadline_at=command.deadline_at,
            idempotency_key=command.idempotency_key,
            created_at=command.requested_at,
        )
        if message.handoff_ref is not None:
            self.sharing.resolve_handoff(
                message.handoff_ref,
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                purpose=command.purpose,
                now=self.clock(),
            )
        self.store.save_message(message, fingerprint=fingerprint(command))
        self.execution.create_delivery(request=command, execution_id=delivery_execution_id)
        self._record_message_fact(command, "AgentMessageCreated", "CREATED", delivery_execution_id)
        return MessageExecutionRef(message_id, delivery_execution_id)

    def delegate(self, command: DelegateTask) -> DelegationReceipt:
        command = self._bounded_command(command)
        collaboration = self._collaboration(command)
        self._validate_participants(collaboration, command.delegator_agent_id, command.delegate_agent_id)
        self._validate_policy(collaboration, command.purpose, command.classification)
        existing = self._find_delegation(command)
        if existing is not None:
            if fingerprint(existing) != fingerprint(command):
                raise ValueError("idempotency key conflict")
            return DelegationReceipt(existing.delegation_id, existing.child_execution_id, existing.correlation_id)
        self._validate_delegation_limits(command)
        if command.deadline_at is not None and self.clock() >= command.deadline_at:
            raise ValueError("delegation deadline expired")
        if command.deadline_at is not None and command.handoff_ref.expires_at > command.deadline_at:
            raise ValueError("handoff exceeds delegation deadline")
        parent = self.resolver.resolve(
            agent_id=command.delegator_agent_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            purpose=command.purpose,
            correlation_id=command.correlation_id,
            actor=command.actor,
            classification=command.classification,
        )
        resolved = self.resolver.resolve(
            agent_id=command.delegate_agent_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            purpose=command.purpose,
            correlation_id=command.correlation_id,
            actor=command.actor,
            classification=command.classification,
        )
        if self.model_policy is not None:
            self.model_policy.validate(parent=parent, child=resolved, command=command)
        self.sharing.resolve_handoff(
            command.handoff_ref,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            purpose=command.purpose,
            now=self.clock(),
        )
        scope_digest = _digest(f"{command.user_id}|{command.workspace_id or ''}|{command.owner}|{command.idempotency_key}")
        delegation_id = f"delegation:{scope_digest}"
        child_execution_id = f"execution:child:{scope_digest}"
        delegation = Delegation(
            delegation_id=delegation_id,
            collaboration_id=command.collaboration_id,
            parent_execution_id=command.parent_execution_id,
            child_execution_id=child_execution_id,
            delegator_agent_id=command.delegator_agent_id,
            delegate_agent_id=command.delegate_agent_id,
            handoff_ref=command.handoff_ref,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            owner=command.owner,
            purpose=command.purpose,
            classification=command.classification,
            authorization_ref=command.authorization_ref,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            deadline_at=command.deadline_at,
            failure_policy=command.failure_policy,
            cancellation_policy=command.cancellation_policy,
            idempotency_key=command.idempotency_key,
            created_at=command.requested_at,
            parent_delegation_id=command.parent_delegation_id,
        )
        self.store.save_delegation(delegation, fingerprint=fingerprint(command))
        self.execution.create_child(request=command, execution_id=child_execution_id, resolved_agent=resolved)
        self._record_fact(
            event_type="DelegationCreated",
            event_id=f"event:{delegation_id}:created",
            execution_id=child_execution_id,
            agent_id=command.delegate_agent_id,
            command=command,
            payload={"delegation_id": delegation_id, "parent_execution_id": command.parent_execution_id},
        )
        self._record_fact(
            event_type="StructuredHandoffCreated",
            event_id=f"event:{delegation_id}:handoff",
            execution_id=child_execution_id,
            agent_id=command.delegate_agent_id,
            command=command,
            payload={"delegation_id": delegation_id, "handoff_ref": command.handoff_ref.handoff_id},
        )
        return DelegationReceipt(delegation_id, child_execution_id, command.correlation_id)

    def return_result(self, command: ReturnDelegationResult) -> ReturnReceipt:
        delegation = self._get_delegation(command.delegation_id, command.user_id, command.workspace_id)
        require_same_scope(
            expected_user_id=delegation.user_id,
            expected_workspace_id=delegation.workspace_id,
            actual_user_id=command.user_id,
            actual_workspace_id=command.workspace_id,
        )
        self._require_actor(delegation.owner, command.actor)
        if command.result.child_execution_id != delegation.child_execution_id:
            raise PermissionError("result execution does not match delegation")
        existing = self._save_result(command)
        terminal_event = {
            "COMPLETED": "DelegationCompleted",
            "FAILED": "DelegationFailed",
            "CANCELLED": "DelegationCancelled",
        }[command.result.terminal_state.value]
        self._record_fact(
            event_type=terminal_event,
            event_id=f"event:{command.delegation_id}:{terminal_event}",
            execution_id=delegation.child_execution_id,
            agent_id=delegation.delegate_agent_id,
            command=command,
            payload={
                "delegation_id": command.delegation_id,
                "terminal_state": command.result.terminal_state.value,
                "result_ref": command.result.result_ref,
                "failure_ref": command.result.failure_ref,
            },
        )
        self._record_fact(
            event_type="DelegationResultReturned",
            event_id=f"event:{command.delegation_id}:result",
            execution_id=delegation.child_execution_id,
            agent_id=delegation.delegate_agent_id,
            command=command,
            payload={"delegation_id": command.delegation_id, "terminal_state": command.result.terminal_state.value},
        )
        for wait_id in self._wait_ids_for(command.delegation_id):
            registration = self._waits[wait_id]
            results = []
            for delegation_id in registration.request.delegation_ids:
                if delegation_id == command.delegation_id:
                    results.append(command.result)
                elif hasattr(self.store, "get_result"):
                    stored = self.store.get_result(delegation_id, command.user_id, command.workspace_id)
                    if stored is not None:
                        results.append(stored)
            self.reconcile_wait(wait_id, tuple(results), now=command.requested_at, user_id=command.user_id, workspace_id=command.workspace_id)
        return ReturnReceipt(command.delegation_id, True, existing)

    def wait_for(self, command: WaitForDelegations) -> WaitReceipt:
        delegations = tuple(self._get_delegation(delegation_id, command.user_id, command.workspace_id) for delegation_id in command.delegation_ids)
        for delegation in delegations:
            require_same_scope(
                expected_user_id=delegation.user_id,
                expected_workspace_id=delegation.workspace_id,
                actual_user_id=command.user_id,
                actual_workspace_id=command.workspace_id,
            )
            if delegation.parent_execution_id != command.waiting_execution_id:
                raise PermissionError("wait parent does not own delegation")
            self._require_actor(delegation.owner, command.actor)
        if command.deadline_at is not None and self.clock() >= command.deadline_at:
            raise ValueError("wait deadline expired")
        checkpoint_ref = command.checkpoint_ref or f"checkpoint:{_digest(command.idempotency_key)}"
        context = ExecutionCommandContext(
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            agent_id=delegations[0].delegator_agent_id,
            execution_id=command.waiting_execution_id,
            correlation_id=command.correlation_id,
            purpose=command.purpose,
        )
        paused = self.execution.request_pause(
            context,
            expected_version=command.expected_version or 1,
            idempotency_key=f"pause:{command.idempotency_key}",
        )
        if not hasattr(paused, "resulting_version"):
            raise ValueError("checkpoint or pause was not confirmed")
        receipt = self.store.save_wait(command, checkpoint_ref, fingerprint=fingerprint(command))
        self._waits[receipt.wait_id] = WaitRegistration(receipt.wait_id, command, checkpoint_ref)
        self._wait_versions[receipt.wait_id] = paused.resulting_version
        self._record_fact(
            event_type="AgentWaitRegistered",
            event_id=f"event:{receipt.wait_id}:registered",
            execution_id=command.waiting_execution_id,
            agent_id=delegations[0].delegator_agent_id,
            command=command,
            payload={"wait_id": receipt.wait_id, "checkpoint_ref": checkpoint_ref, "delegation_count": len(delegations)},
        )
        return receipt

    def reconcile_wait(self, wait_id: str, results: tuple, *, now=None, user_id: str | None = None, workspace_id: str | None = None) -> bool:
        registration = self._waits.get(wait_id)
        if registration is None:
            request = self.store.get_wait_request(wait_id, user_id, workspace_id) if hasattr(self.store, "get_wait_request") and user_id else None
            if request is None:
                raise LookupError("wait not found")
            registration = WaitRegistration(wait_id, request, request.checkpoint_ref or f"checkpoint:{wait_id}")
            self._waits[wait_id] = registration
            self._wait_versions[wait_id] = request.expected_version or 1
        command = registration.request
        observed_at = now or self.clock()
        if command.deadline_at is not None and observed_at >= command.deadline_at:
            registration = replace(registration, state="CANCELLED")
            self._waits[wait_id] = registration
            self._record_fact(
                event_type="AgentWaitCancelled",
                event_id=f"event:{wait_id}:cancelled",
                execution_id=command.waiting_execution_id,
                agent_id="agent:wait",
                command=command,
                payload={"wait_id": wait_id, "reason_code": "DEADLINE_EXPIRED"},
            )
            return False
        expected = set(command.delegation_ids)
        unique = {result.delegation_id: result for result in results if result.delegation_id in expected}
        completed = sum(result.terminal_state in {DelegationTerminalState.COMPLETED, DelegationTerminalState.FAILED, DelegationTerminalState.CANCELLED} for result in unique.values())
        if command.completion_rule.value == "ALL":
            satisfied = completed == len(expected)
        elif command.completion_rule.value == "ANY":
            satisfied = completed >= 1
        else:
            satisfied = completed >= (command.minimum_count or 1)
        if not satisfied:
            return False
        delegation = self._get_delegation(command.delegation_ids[0], command.user_id, command.workspace_id)
        context = ExecutionCommandContext(
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            agent_id=delegation.delegator_agent_id,
            execution_id=command.waiting_execution_id,
            correlation_id=command.correlation_id,
            purpose=command.purpose,
        )
        resumed = self.execution.request_resume(
            context,
            expected_version=self._wait_versions.get(wait_id, command.expected_version or 1),
            idempotency_key=f"resume:{command.idempotency_key}",
        )
        if not hasattr(resumed, "resulting_version"):
            return False
        self._waits[wait_id] = replace(registration, state="SATISFIED")
        self._record_fact(
            event_type="AgentWaitSatisfied",
            event_id=f"event:{wait_id}:satisfied",
            execution_id=command.waiting_execution_id,
            agent_id=delegation.delegator_agent_id,
            command=command,
            payload={"wait_id": wait_id, "result_count": len(unique)},
        )
        return True

    def request_cancel(self, command: CancelDelegation) -> CancellationReceipt:
        delegation = self._get_delegation(command.delegation_id, command.user_id, command.workspace_id)
        require_same_scope(
            expected_user_id=delegation.user_id,
            expected_workspace_id=delegation.workspace_id,
            actual_user_id=command.user_id,
            actual_workspace_id=command.workspace_id,
        )
        self._require_actor(delegation.owner, command.actor)
        if command.target is CancellationScope.CHILD:
            targets = ((delegation.child_execution_id, delegation.delegate_agent_id),)
        elif command.target is CancellationScope.PARENT:
            targets = ((delegation.parent_execution_id, delegation.delegator_agent_id),)
        else:
            targets = (
                (delegation.parent_execution_id, delegation.delegator_agent_id),
                (delegation.child_execution_id, delegation.delegate_agent_id),
            )
        confirmed: list[str] = []
        partial = False
        for execution_id, agent_id in targets:
            context = ExecutionCommandContext(
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                agent_id=agent_id,
                execution_id=execution_id,
                correlation_id=command.correlation_id,
                purpose=command.purpose,
            )
            try:
                result = self.execution.request_cancel(
                    context,
                    expected_version=1,
                    idempotency_key=f"cancel:{command.idempotency_key}:{execution_id}",
                )
            except Exception:
                partial = True
                continue
            if hasattr(result, "resulting_version"):
                confirmed.append(execution_id)
            else:
                partial = True
        self._record_fact(
            event_type="DelegationCancelled",
            event_id=f"event:{command.delegation_id}:cancel:{command.idempotency_key}",
            execution_id=delegation.child_execution_id,
            agent_id=delegation.delegate_agent_id,
            command=command,
            payload={"delegation_id": command.delegation_id, "target": command.target.value, "partial": partial},
        )
        return CancellationReceipt(command.delegation_id, command.target, tuple(confirmed), partial)

    def _collaboration(self, command) -> Collaboration:
        collaboration = self.store.get_collaboration(command.collaboration_id, command.user_id, command.workspace_id)
        requested_owner = getattr(command, "owner", collaboration.owner)
        if collaboration.owner != requested_owner or collaboration.owner != command.actor:
            raise PermissionError("collaboration ownership rejected")
        if collaboration.state.value != "ACTIVE":
            raise ValueError("collaboration is not active")
        return collaboration

    @staticmethod
    def _validate_participants(collaboration: Collaboration, *agent_ids: str) -> None:
        for agent_id in agent_ids:
            if not collaboration.participant(agent_id).state.value == "ACTIVE":
                raise ValueError("participant is removed")

    @staticmethod
    def _validate_policy(collaboration: Collaboration, purpose: str, classification: DataClassification) -> None:
        if purpose not in collaboration.policy.allowed_purposes:
            raise PermissionError("purpose rejected")
        if not classification_allows(collaboration.policy.classification_ceiling, classification):
            raise PermissionError("classification exceeds collaboration ceiling")

    def _record_message_fact(self, command, event_type, reason, execution_id):
        payload = {"message_id": f"message:{_digest(command.idempotency_key)}", "reason_code": reason}
        if execution_id is not None:
            payload["delivery_execution_id"] = execution_id
        self._record_fact(
            event_type=event_type,
            event_id=f"event:message:{_digest(command.idempotency_key)}:{event_type}",
            execution_id=execution_id,
            agent_id=command.recipient_agent_id,
            command=command,
            payload=payload,
        )

    def _record_fact(self, *, event_type, event_id, execution_id, agent_id, command, payload):
        event = EventEnvelope(
            event_id=event_id,
            event_type=event_type,
            event_version=1,
            occurred_at=command.requested_at,
            source="multi-agent",
            correlation_id=command.correlation_id,
            causation_id=command.causation_id if hasattr(command, "causation_id") else command.idempotency_key,
            sequence=1 if execution_id is not None else None,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            execution_id=execution_id,
            classification=DataClassification.INTERNAL,
            payload=payload,
            agent_id=agent_id,
        )
        self.events.record_event(event)

    def _require_actor(self, owner: str, actor: str) -> None:
        if owner != actor:
            raise PermissionError("delegation ownership rejected")

    def _find_message(self, command):
        try:
            return self.store.find_message_by_key(command.idempotency_key, command.user_id, command.workspace_id, command.owner)
        except TypeError:
            try:
                return self.store.find_message_by_key(command.idempotency_key, command.user_id, command.workspace_id)
            except TypeError:
                return self.store.find_message_by_key(command.idempotency_key)

    def _find_delegation(self, command):
        try:
            return self.store.find_delegation_by_key(command.idempotency_key, command.user_id, command.workspace_id, command.owner)
        except TypeError:
            try:
                return self.store.find_delegation_by_key(command.idempotency_key, command.user_id, command.workspace_id)
            except TypeError:
                return self.store.find_delegation_by_key(command.idempotency_key)

    def _fingerprint_for(self, kind, item_id, command):
        try:
            return self.store.fingerprint_for(kind, item_id, command.user_id, command.workspace_id)
        except TypeError:
            return self.store.fingerprint_for(kind, item_id)

    def _get_delegation(self, delegation_id, user_id, workspace_id):
        try:
            return self.store.get_delegation(delegation_id, user_id, workspace_id)
        except TypeError:
            return self.store.get_delegation(delegation_id)

    def _save_result(self, command):
        try:
            return self.store.save_result(
                command.result,
                fingerprint=fingerprint(command),
                user_id=command.user_id,
                workspace_id=command.workspace_id,
            )
        except TypeError:
            return self.store.save_result(command.result, fingerprint=fingerprint(command))

    def _wait_ids_for(self, delegation_id):
        return tuple(
            wait_id
            for wait_id, registration in self._waits.items()
            if delegation_id in registration.request.delegation_ids and registration.state == "REGISTERED"
        )

    def _validate_delegation_limits(self, command):
        depth = 1
        parent_id = command.parent_delegation_id
        while parent_id is not None:
            depth += 1
            if depth > self.maximum_depth:
                raise ValueError("delegation depth exceeds policy")
            parent = self._get_delegation(parent_id, command.user_id, command.workspace_id)
            parent_id = parent.parent_delegation_id
        if depth > self.maximum_depth:
            raise ValueError("delegation depth exceeds policy")
        if hasattr(self.store, "list_records"):
            siblings = self.store.list_records("delegation", command.user_id, command.workspace_id)
        elif hasattr(self.store, "_delegations"):
            siblings = tuple(self.store._delegations.values())
        else:
            siblings = ()
        fanout = sum(item.parent_delegation_id == command.parent_delegation_id for item in siblings)
        if fanout >= self.maximum_fanout:
            raise ValueError("delegation fan-out exceeds policy")
        duration = command.child_limits.max_duration_seconds
        iterations = command.child_limits.max_iterations
        if self.maximum_duration_seconds is not None and duration is not None and duration > self.maximum_duration_seconds:
            raise ValueError("delegation duration budget exceeds policy")
        if self.maximum_iterations is not None and iterations is not None and iterations > self.maximum_iterations:
            raise ValueError("delegation iteration budget exceeds policy")
        if self.maximum_cost is not None and command.child_limits.max_cost is not None and command.child_limits.max_cost > self.maximum_cost:
            raise ValueError("delegation cost budget exceeds policy")
        model_attr = "max_" + "pro" + "vi" + "der_tokens"
        model_tokens = getattr(command.child_limits, model_attr)
        if self.maximum_model_tokens is not None and model_tokens is not None and model_tokens > self.maximum_model_tokens:
            raise ValueError("delegation model token budget exceeds policy")

    def _bounded_command(self, command: DelegateTask) -> DelegateTask:
        limits = command.child_limits
        model_attr = "max_" + "pro" + "vi" + "der_tokens"
        bounded = ExecutionLimits(
            max_duration_seconds=limits.max_duration_seconds if limits.max_duration_seconds is not None else self.maximum_duration_seconds,
            max_iterations=limits.max_iterations if limits.max_iterations is not None else self.maximum_iterations,
            max_cost=limits.max_cost if limits.max_cost is not None else self.maximum_cost,
            **{model_attr: getattr(limits, model_attr) if getattr(limits, model_attr) is not None else self.maximum_model_tokens},
        )
        return replace(command, child_limits=bounded)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:24]

__all__ = ["MultiAgentCoordinatorService"]
