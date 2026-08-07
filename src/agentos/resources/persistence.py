from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence.models import AuditChange, OutboxChange, PersistenceOperationContext, RecordChange, RecordReference, TransactionOptions, TransactionRequest


class ResourcePersistenceJournal:
    def __init__(self, persistence) -> None:
        self.persistence = persistence

    def record_fact(self, *, user_id, workspace_id, agent_id, execution_id, correlation_id, purpose, actor, lease_id, resource_type, state, outcome):
        context = PersistenceOperationContext(user_id, workspace_id, agent_id, execution_id, correlation_id, purpose, actor)
        record_ref = RecordReference(f"resource-lease:{lease_id}")
        payload = {"lease_id": lease_id, "resource_type": resource_type, "state": state, "outcome": outcome, "workspace_id": workspace_id, "execution_id": execution_id}
        event = EventEnvelope(f"resource-event:{lease_id}", "ResourceLeaseGranted", 1, datetime.now(timezone.utc), "resource_manager", correlation_id, lease_id, 1, user_id, workspace_id, execution_id, DataClassification.INTERNAL, payload, agent_id)
        change = RecordChange(record_ref, "resource_lease", None, payload, DataClassification.INTERNAL)
        audit = AuditChange(f"audit:{lease_id}", record_ref, outcome, 1, {"decision": outcome, "purpose": purpose})
        request = TransactionRequest(f"tx:resource:{lease_id}", context, TransactionOptions(), lease_id, sha256(repr(payload).encode()).hexdigest(), (), (change,), (audit,), (OutboxChange(event, record_ref, 1),))
        return self.persistence.transact(request)


__all__ = ["ResourcePersistenceJournal"]
