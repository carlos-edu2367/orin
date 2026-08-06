from __future__ import annotations

from dataclasses import replace

from .models import Collaboration, CollaborationParticipant, ParticipantState
from .ports import CommitReceipt, CommitState, MultiAgentEventRecorder
from agentos.events import EventEnvelope


class InMemoryMultiAgentStore(MultiAgentEventRecorder):
    """Process-local reference adapter; it is not a durability guarantee."""

    def __init__(self) -> None:
        self._collaborations: dict[str, Collaboration] = {}
        self._collaboration_keys: dict[tuple[str, str | None, str], str] = {}
        self._messages: dict[str, object] = {}
        self._delegations: dict[str, object] = {}
        self._results: dict[str, object] = {}
        self._waits: dict[str, object] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._events: dict[str, EventEnvelope] = {}
        self._transactions: dict[tuple[str, str], CommitReceipt] = {}

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        return tuple(self._events.values())

    def save_collaboration(self, collaboration: Collaboration, *, idempotency_key: str) -> Collaboration:
        key = (collaboration.user_id, collaboration.workspace_id, idempotency_key)
        existing_id = self._collaboration_keys.get(key)
        if existing_id is not None:
            existing = self._collaborations[existing_id]
            if existing != collaboration:
                raise ValueError("idempotency key conflict")
            return existing
        if collaboration.collaboration_id in self._collaborations:
            raise ValueError("collaboration already exists")
        self._collaborations[collaboration.collaboration_id] = collaboration
        self._collaboration_keys[key] = collaboration.collaboration_id
        return collaboration

    def get_collaboration(self, collaboration_id: str, user_id: str, workspace_id: str | None) -> Collaboration:
        collaboration = self._collaborations.get(collaboration_id)
        if collaboration is None or collaboration.user_id != user_id or collaboration.workspace_id != workspace_id:
            raise PermissionError("collaboration not found")
        return collaboration

    def add_participant(self, collaboration_id: str, agent_id: str, now, *, idempotency_key: str) -> Collaboration:
        current = self._collaborations[collaboration_id]
        if agent_id in current.participant_agent_ids:
            return current
        if len(current.participant_agent_ids) >= current.policy.maximum_participants:
            raise ValueError("participant limit exceeded")
        updated = replace(
            current,
            participant_agent_ids=current.participant_agent_ids + (agent_id,),
            participant_records=current.participant_records + (CollaborationParticipant(agent_id, ParticipantState.ACTIVE, now),),
            version=current.version + 1,
        )
        self._collaborations[collaboration_id] = updated
        return updated

    def remove_participant(self, collaboration_id: str, agent_id: str, now, *, idempotency_key: str) -> Collaboration:
        current = self._collaborations[collaboration_id]
        participant = current.participant(agent_id)
        if participant.state is ParticipantState.REMOVED:
            return current
        records = tuple(
            replace(item, state=ParticipantState.REMOVED, removed_at=now) if item.agent_id == agent_id else item
            for item in current.participant_records
        )
        updated = replace(current, participant_records=records, version=current.version + 1)
        self._collaborations[collaboration_id] = updated
        return updated

    def can_accept(self, collaboration_id: str, agent_id: str) -> bool:
        return self._collaborations[collaboration_id].participant(agent_id).state is ParticipantState.ACTIVE

    def save_message(self, message, *, fingerprint: str):
        return self._save("message", self._messages, message.message_id, message.idempotency_key, fingerprint, message)

    def save_delegation(self, delegation, *, fingerprint: str):
        return self._save("delegation", self._delegations, delegation.delegation_id, delegation.idempotency_key, fingerprint, delegation)

    def save_result(self, result, *, fingerprint: str):
        return self._save("result", self._results, result.delegation_id, result.delegation_id, fingerprint, result)

    def save_wait(self, command, checkpoint_ref: str, *, fingerprint: str):
        from .models import WaitReceipt

        wait_id = f"wait:{command.idempotency_key}"
        existing = self._waits.get(wait_id)
        if existing is not None:
            if self._fingerprints[("wait", wait_id)] != fingerprint:
                raise ValueError("idempotency key conflict")
            return existing
        receipt = WaitReceipt(wait_id, command.waiting_execution_id, checkpoint_ref)
        self._waits[wait_id] = receipt
        self._fingerprints[("wait", wait_id)] = fingerprint
        return receipt

    def _save(self, kind, bucket, item_id, idempotency_key, item_fingerprint, value):
        existing = bucket.get(item_id)
        if existing is not None:
            if self._fingerprints[(kind, item_id)] != item_fingerprint:
                raise ValueError("idempotency key conflict")
            return existing
        bucket[item_id] = value
        self._fingerprints[(kind, item_id)] = item_fingerprint
        return value

    def record_event(self, event: EventEnvelope) -> bool:
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise ValueError("event_id already exists with a different event")
            return False
        self._events[event.event_id] = event
        return True

    def begin_commit(self, transaction_id: str, idempotency_key: str, *, unknown: bool = False) -> CommitReceipt:
        key = (transaction_id, idempotency_key)
        if key in self._transactions:
            return self._transactions[key]
        receipt = CommitReceipt(transaction_id, idempotency_key, CommitState.UNKNOWN if unknown else CommitState.COMMITTED)
        self._transactions[key] = receipt
        return receipt

    def inspect_commit(self, transaction_id: str, idempotency_key: str) -> CommitReceipt:
        key = (transaction_id, idempotency_key)
        receipt = self._transactions.get(key)
        if receipt is None:
            raise LookupError("transaction not found")
        if receipt.commit_state is CommitState.UNKNOWN:
            receipt = replace(receipt, commit_state=CommitState.COMMITTED)
            self._transactions[key] = receipt
        return receipt


__all__ = ["InMemoryMultiAgentStore"]
