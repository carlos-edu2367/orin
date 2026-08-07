from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence.models import OutboxChange, PersistenceOperationContext, RecordChange, RecordReference, TransactionRequest, TransactionOptions

from .models import BrowserOperationContext, BrowserSessionSnapshot


def _persistence_context(context: BrowserOperationContext) -> PersistenceOperationContext:
    return PersistenceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, context.purpose, context.actor)


def sanitized_event_payload(event_type: str, snapshot: BrowserSessionSnapshot) -> dict[str, object]:
    return {"event_type": event_type, "session_id": snapshot.session_id, "profile_id": snapshot.profile_id, "status": snapshot.status.value, "version": snapshot.version, "page_count": len(snapshot.page_ids)}


class BrowserPersistenceJournal:
    def __init__(self, persistence) -> None:
        self.persistence = persistence
        self._snapshots: dict[tuple[tuple[str, ...], str], BrowserSessionSnapshot] = {}

    def record(self, snapshot: BrowserSessionSnapshot, context: BrowserOperationContext, operation_id: str, event_type: str):
        record_ref = RecordReference(f"browser-session:{snapshot.session_id}")
        payload = sanitized_event_payload(event_type, snapshot)
        payload["workspace_id"] = snapshot.context.workspace_id or ""
        event = EventEnvelope(
            event_id=f"browser-event:{operation_id}", event_type=event_type, event_version=1, occurred_at=datetime.now(timezone.utc), source="agentos.browser", correlation_id=context.correlation_id, causation_id=None, sequence=1, user_id=context.user_id, workspace_id=context.workspace_id, execution_id=context.execution_id, classification=DataClassification.INTERNAL, payload=payload, agent_id=context.agent_id,
        )
        change = RecordChange(record_ref, "BrowserSessionSnapshot", None, payload, DataClassification.INTERNAL)
        request = TransactionRequest(operation_id, _persistence_context(context), TransactionOptions(), f"browser:{snapshot.session_id}:{operation_id}", hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), (), (change,), (), (OutboxChange(event, record_ref, 1),))
        result = self.persistence.transact(request)
        self._snapshots[(context.scope_key(), snapshot.session_id)] = snapshot
        return result.receipt if hasattr(result, "receipt") else result

    def load(self, context: BrowserOperationContext, session_id: str) -> BrowserSessionSnapshot:
        return self._snapshots[(context.scope_key(), session_id)]


__all__ = ["BrowserPersistenceJournal", "sanitized_event_payload"]
