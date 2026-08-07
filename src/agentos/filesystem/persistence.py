from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence.models import AuditChange, OutboxChange, PersistenceOperationContext, RecordChange, RecordReference, TransactionOptions, TransactionRequest


class FilesystemPersistenceJournal:
    def __init__(self, persistence) -> None:
        self.persistence = persistence

    def record_fact(self, *, user_id, workspace_id, agent_id, execution_id, correlation_id, purpose, actor, operation_id, event_type, outcome, version):
        context = PersistenceOperationContext(user_id, workspace_id, agent_id, execution_id, correlation_id, purpose, actor)
        record_ref = RecordReference(f"filesystem-operation:{workspace_id}:{operation_id}")
        payload = {"operation_id": operation_id, "workspace_id": workspace_id, "execution_id": execution_id, "outcome": outcome, "version": version}
        event = EventEnvelope(f"filesystem-event:{operation_id}", event_type, 1, datetime.now(timezone.utc), "filesystem", correlation_id, operation_id, 1, user_id, workspace_id, execution_id, DataClassification.INTERNAL, payload, agent_id)
        change = RecordChange(record_ref, "filesystem_operation", None, payload, DataClassification.INTERNAL)
        audit = AuditChange(f"audit:{operation_id}", record_ref, outcome, 1, {"decision": outcome, "purpose": purpose})
        request = TransactionRequest(f"tx:filesystem:{operation_id}", context, TransactionOptions(), operation_id, sha256(repr(payload).encode()).hexdigest(), (), (change,), (audit,), (OutboxChange(event, record_ref, 1),))
        return self.persistence.transact(request)


__all__ = ["FilesystemPersistenceJournal"]
