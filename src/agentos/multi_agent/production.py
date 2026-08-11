"""Port-backed durable adapters for the multi-agent domain.

The adapter deliberately depends on the public persistence port.  It does not
know SQLAlchemy, a broker, or a transport.  Deployments can therefore supply
the existing PostgreSQL persistence adapter while tests can use the canonical
in-memory persistence implementation.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from agentos.context import HandoffRef
from agentos.events import DataClassification
from agentos.persistence import (
    AuthorizedRead,
    AuthorizedScan,
    AuthorizedRecord,
    ConsistencyLevel,
    PageRequest,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionOptions,
    TransactionRequest,
)

from .models import (
    AgentMessage,
    AgentMessageKind,
    Collaboration,
    CollaborationParticipant,
    CollaborationPolicy,
    CollaborationState,
    CompletionRule,
    Delegation,
    DelegationCancellationPolicy,
    DelegationFailurePolicy,
    DelegationResult,
    DelegationTerminalState,
    ParticipantState,
    WaitReceipt,
    WaitForDelegations,
)


_ACTOR = "multi-agent-store"
_AGENT = "multi-agent-store"
_EXECUTION = "multi-agent-store"
_CORRELATION = "multi-agent-store"
_PURPOSE = "multi-agent.store"
_TYPES = {
    "collaboration": "multi_agent.collaboration",
    "message": "multi_agent.message",
    "delegation": "multi_agent.delegation",
    "result": "multi_agent.result",
    "wait": "multi_agent.wait",
}


def _context(user_id: str, workspace_id: str | None) -> PersistenceOperationContext:
    return PersistenceOperationContext(
        user_id=user_id,
        workspace_id=workspace_id,
        agent_id=_AGENT,
        execution_id=_EXECUTION,
        correlation_id=_CORRELATION,
        purpose=_PURPOSE,
        actor=_ACTOR,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, HandoffRef):
        return {"__handoff_ref__": _jsonable({field.name: getattr(value, field.name) for field in fields(value)})}
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _decode_datetime(value: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(value["__datetime__"])


def _decode_handoff(value: dict[str, Any]) -> HandoffRef:
    data = {key: item for key, item in value["__handoff_ref__"].items()}
    data["expires_at"] = _decode_datetime(data["expires_at"])
    data["classification"] = DataClassification(data["classification"])
    return HandoffRef(**data)


def _restore(value: Any) -> Any:
    if isinstance(value, Mapping):
        value = dict(value)
    if isinstance(value, dict) and "__datetime__" in value:
        return _decode_datetime(value)
    if isinstance(value, dict) and "__handoff_ref__" in value:
        return _decode_handoff(value)
    if isinstance(value, list):
        return tuple(_restore(item) for item in value)
    if isinstance(value, Mapping):
        return {key: _restore(item) for key, item in value.items()}
    return value


def _pack(value: Any, fingerprint: str, owner: str | None = None) -> dict[str, Any]:
    packed = {"fingerprint": fingerprint, "value": _jsonable(value)}
    if owner is not None:
        packed["owner"] = owner
    return packed


class DurableMultiAgentStore:
    """Durable ``CollaborationStore`` implemented through RFC 601 ports."""

    def __init__(self, persistence, *, event_recorder=None) -> None:
        self.persistence = persistence
        self.event_recorder = event_recorder

    def save_collaboration(self, collaboration: Collaboration, *, idempotency_key: str) -> Collaboration:
        return self._save("collaboration", collaboration.collaboration_id, collaboration, idempotency_key, collaboration.user_id, collaboration.workspace_id, owner=collaboration.owner)

    def get_collaboration(self, collaboration_id: str, user_id: str, workspace_id: str | None) -> Collaboration:
        value = self._read("collaboration", collaboration_id, user_id, workspace_id)
        if value is None:
            raise PermissionError("collaboration not found")
        return value

    def save_message(self, message: AgentMessage, *, fingerprint: str) -> AgentMessage:
        return self._save("message", message.message_id, message, message.idempotency_key, message.user_id, message.workspace_id, fingerprint=fingerprint, owner=message.owner)

    def find_message_by_key(self, key: str, user_id: str | None = None, workspace_id: str | None = None, owner: str | None = None):
        return self._find_by_key("message", key, user_id, workspace_id, owner)

    def save_delegation(self, delegation: Delegation, *, fingerprint: str) -> Delegation:
        return self._save("delegation", delegation.delegation_id, delegation, delegation.idempotency_key, delegation.user_id, delegation.workspace_id, fingerprint=fingerprint, owner=delegation.owner)

    def find_delegation_by_key(self, key: str, user_id: str | None = None, workspace_id: str | None = None, owner: str | None = None):
        return self._find_by_key("delegation", key, user_id, workspace_id, owner)

    def get_delegation(self, delegation_id: str, user_id: str | None = None, workspace_id: str | None = None) -> Delegation:
        if user_id is None:
            raise PermissionError("delegation scope is required")
        value = self._read("delegation", delegation_id, user_id, workspace_id)
        if value is None:
            raise PermissionError("delegation not found")
        return value

    def save_result(self, result: DelegationResult, *, fingerprint: str, user_id: str | None = None, workspace_id: str | None = None) -> DelegationResult:
        if user_id is None:
            raise PermissionError("result scope is required")
        return self._save("result", result.delegation_id, result, result.delegation_id, user_id, workspace_id, fingerprint=fingerprint)

    def get_result(self, delegation_id: str, user_id: str, workspace_id: str | None = None):
        return self._read("result", delegation_id, user_id, workspace_id)

    def save_wait(self, command, checkpoint_ref: str, *, fingerprint: str) -> WaitReceipt:
        receipt = WaitReceipt(f"wait:{command.idempotency_key}", command.waiting_execution_id, checkpoint_ref)
        return self._save("wait", receipt.wait_id, {"receipt": receipt, "request": command}, command.idempotency_key, command.user_id, command.workspace_id, fingerprint=fingerprint, owner=command.actor)["receipt"]

    def get_wait(self, wait_id: str, user_id: str, workspace_id: str | None = None):
        value = self._read("wait", wait_id, user_id, workspace_id)
        return value["receipt"] if isinstance(value, dict) and "receipt" in value else value

    def get_wait_request(self, wait_id: str, user_id: str, workspace_id: str | None = None):
        value = self._read("wait", wait_id, user_id, workspace_id)
        if not isinstance(value, dict) or "request" not in value:
            return None
        request = dict(value["request"])
        request["completion_rule"] = CompletionRule(request["completion_rule"])
        return WaitForDelegations(**request)

    def list_records(self, kind: str, user_id: str, workspace_id: str | None = None, owner: str | None = None) -> tuple[Any, ...]:
        query = AuthorizedScan(
            context=_context(user_id, workspace_id),
            record_type=_TYPES[kind],
            filters={},
            classification_ceiling=DataClassification.RESTRICTED,
            page=PageRequest(limit=100),
            consistency=ConsistencyLevel.STRONG,
        )
        page = self.persistence.scan(query)
        records = tuple(record for record in page.items if owner is None or record.data.get("owner") == owner)
        return tuple(self._unpack(record) for record in records)

    def fingerprint_for(self, kind: str, item_id: str, user_id: str, workspace_id: str | None = None) -> str | None:
        value = self._raw_read(kind, item_id, user_id, workspace_id)
        return None if value is None else value.data.get("fingerprint")

    def record_event(self, event) -> bool:
        if self.event_recorder is None:
            return True
        return self.event_recorder.record_event(event)

    def _save(self, kind, item_id, value, idempotency_key, user_id, workspace_id, *, fingerprint: str | None = None, owner: str | None = None):
        existing = self._raw_read(kind, item_id, user_id, workspace_id)
        actual_fingerprint = fingerprint or _fingerprint(value)
        if existing is not None:
            if existing.data.get("fingerprint") != actual_fingerprint:
                raise ValueError("idempotency key conflict")
            return self._unpack(existing)
        reference = RecordReference(f"multi-agent:{kind}:{item_id}")
        change = RecordChange(
            record_ref=reference,
            record_type=_TYPES[kind],
            expected_version=None,
            data=_pack(value, actual_fingerprint, owner),
            classification=getattr(value, "classification", DataClassification.INTERNAL),
        )
        request = TransactionRequest(
            transaction_id=f"multi-agent:{kind}:{item_id}",
            context=_context(user_id, workspace_id),
            options=TransactionOptions(),
            idempotency_key=f"{kind}:{idempotency_key}",
            fingerprint=actual_fingerprint,
            expected_versions=(),
            changes=(change,),
            audit=(),
            outbox=(),
        )
        result = self.persistence.transact(request)
        if not hasattr(result, "records"):
            raise ValueError("multi-agent persistence rejected")
        return value if not result.already_applied else self._unpack(result.records[0])

    def _raw_read(self, kind: str, item_id: str, user_id: str, workspace_id: str | None) -> AuthorizedRecord | None:
        result = self.persistence.read(
            AuthorizedRead(
                context=_context(user_id, workspace_id),
                record_ref=RecordReference(f"multi-agent:{kind}:{item_id}"),
                record_type=_TYPES[kind],
                classification_ceiling=DataClassification.RESTRICTED,
            )
        )
        return result if isinstance(result, AuthorizedRecord) else None

    def _read(self, kind: str, item_id: str, user_id: str, workspace_id: str | None):
        record = self._raw_read(kind, item_id, user_id, workspace_id)
        return None if record is None else self._unpack(record)

    def _find_by_key(self, kind, key, user_id, workspace_id, owner=None):
        if user_id is None:
            raise PermissionError("query scope is required")
        for value in self.list_records(kind, user_id, workspace_id, owner):
            if getattr(value, "idempotency_key", None) == key:
                return value
        return None

    @staticmethod
    def _unpack(record: AuthorizedRecord):
        value = _restore(record.data["value"])
        kind = record.record_type.rsplit(".", 1)[-1]
        if kind == "collaboration":
            value["policy"] = CollaborationPolicy(**value["policy"])
            value["participant_records"] = tuple(CollaborationParticipant(**item) for item in value["participant_records"])
            value["state"] = CollaborationState(value["state"])
            return Collaboration(**value)
        if kind == "message":
            value["kind"] = AgentMessageKind(value["kind"])
            value["classification"] = DataClassification(value["classification"])
            return AgentMessage(**value)
        if kind == "delegation":
            value["classification"] = DataClassification(value["classification"])
            value["failure_policy"] = DelegationFailurePolicy(value["failure_policy"])
            value["cancellation_policy"] = DelegationCancellationPolicy(value["cancellation_policy"])
            return Delegation(**value)
        if kind == "result":
            value["terminal_state"] = DelegationTerminalState(value["terminal_state"])
            value["failure_policy"] = DelegationFailurePolicy(value["failure_policy"])
            return DelegationResult(**value)
        if kind == "wait" and isinstance(value, dict) and "receipt" in value:
            value["receipt"] = WaitReceipt(**value["receipt"])
            value["request"] = WaitForDelegations(**{**value["request"], "completion_rule": CompletionRule(value["request"]["completion_rule"])})
            return value
        return WaitReceipt(**value)


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


PostgresMultiAgentStore = DurableMultiAgentStore

__all__ = ["DurableMultiAgentStore", "PostgresMultiAgentStore"]
