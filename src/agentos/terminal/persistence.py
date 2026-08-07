from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence.models import (
    AuthorizedRead,
    CommitState,
    InspectCommit,
    OutboxChange,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionOptions,
    TransactionRequest,
    TransactionReceipt,
    TransactionCommitted,
    TransactionIndeterminate,
)

from .models import BufferTruncation, TerminalBuffer, TerminalOperationContext, TerminalSessionSnapshot, TerminalSessionStatus


class TerminalPersistenceJournal:
    """Stores bounded terminal facts and the matching event in one transaction."""

    RECORD_TYPE = "terminal_session"

    def __init__(self, persistence) -> None:
        self.persistence = persistence
        self._versions: dict[str, int] = {}
        self._sequences: dict[str, int] = {}

    @staticmethod
    def _context(context: TerminalOperationContext) -> PersistenceOperationContext:
        return PersistenceOperationContext(*context.scope_key())

    @staticmethod
    def _data(snapshot: TerminalSessionSnapshot) -> dict[str, object]:
        return {
            "session_id": str(snapshot.id),
            "cwd": snapshot.cwd.as_logical_string(),
            "status": snapshot.status.value,
            "owner": snapshot.owner,
            "workspace": snapshot.workspace,
            "agent_id": snapshot.agent_id,
            "execution_id": snapshot.execution_id,
            "correlation_id": snapshot.correlation_id,
            "purpose": snapshot.purpose,
            "lease_id": str(snapshot.lease_id),
            "current_command_id": str(snapshot.current_command_id) if snapshot.current_command_id is not None else None,
            "policy_version": snapshot.policy_version,
            "created_at": snapshot.created_at.isoformat(),
            "last_activity_at": snapshot.last_activity_at.isoformat(),
            "expires_at": snapshot.expires_at.isoformat(),
            "buffer": {
                "first_sequence": snapshot.buffer.first_sequence,
                "last_sequence": snapshot.buffer.last_sequence,
                "retained_bytes": snapshot.buffer.retained_bytes,
                "dropped_bytes": snapshot.buffer.dropped_bytes,
                "maximum_bytes": snapshot.buffer.maximum_bytes,
                "truncation": snapshot.buffer.truncation.value,
            },
            "output_ref": str(snapshot.output_ref) if snapshot.output_ref is not None else None,
        }

    def authorized_read(self, context: TerminalOperationContext, session_id: str) -> AuthorizedRead:
        return AuthorizedRead(self._context(context), RecordReference(session_id), self.RECORD_TYPE, DataClassification.RESTRICTED)

    def record(self, snapshot: TerminalSessionSnapshot, *, context: TerminalOperationContext, operation_id: str, event_type: str | None) -> TransactionReceipt:
        record_ref = RecordReference(str(snapshot.id))
        version = self._versions.get(str(snapshot.id))
        payload = self._data(snapshot)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        persistence_context = self._context(context)
        event = None
        outbox = ()
        if event_type is not None:
            sequence = self._sequences.get(context.execution_id, 0) + 1
            self._sequences[context.execution_id] = sequence
            event = EventEnvelope(
                event_id=f"terminal-persisted:{snapshot.id}:{operation_id}",
                event_type=event_type,
                event_version=1,
                occurred_at=snapshot.last_activity_at,
                source="terminal",
                correlation_id=context.correlation_id,
                causation_id=operation_id,
                sequence=sequence,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                agent_id=context.agent_id,
                execution_id=context.execution_id,
                classification=DataClassification.INTERNAL,
                payload={"session_id": str(snapshot.id), "status": snapshot.status.value},
            )
            outbox = (OutboxChange(event, record_ref, (version or 0) + 1),)
        request = TransactionRequest(
            transaction_id=f"tx:terminal:{snapshot.id}:{operation_id}",
            context=persistence_context,
            options=TransactionOptions(),
            idempotency_key=operation_id,
            fingerprint=fingerprint,
            expected_versions=(),
            changes=(RecordChange(record_ref, self.RECORD_TYPE, version, payload, DataClassification.INTERNAL),),
            audit=(),
            outbox=outbox,
        )
        result = self.persistence.transact(request)
        if isinstance(result, TransactionIndeterminate):
            receipt = self.persistence.inspect_commit(InspectCommit(persistence_context, result.transaction_id, operation_id))
            if receipt.commit_state is CommitState.COMMITTED:
                self._versions[str(snapshot.id)] = receipt.records[0].version if receipt.records else (version or 0) + 1
            return receipt
        if isinstance(result, TransactionCommitted):
            self._versions[str(snapshot.id)] = result.receipt.records[0].version if result.receipt.records else (version or 0) + 1
            return result.receipt
        receipt = getattr(result, "receipt", None)
        return receipt or TransactionReceipt(request.transaction_id, CommitState.NOT_COMMITTED, (), (), 0, None)

    def load(self, context: TerminalOperationContext, session_id: str) -> TerminalSessionSnapshot | None:
        record = self.persistence.read(self.authorized_read(context, session_id))
        if not hasattr(record, "data"):
            return None
        data = record.data
        buffer = data["buffer"]
        cwd = data["cwd"]
        from agentos.filesystem.models import WorkspacePath
        path = WorkspacePath.root() if cwd == "" else WorkspacePath.from_string(cwd)
        return TerminalSessionSnapshot(
            data["session_id"], path, None, TerminalSessionStatus(data["status"]), data["owner"], data["workspace"], data["agent_id"], data["execution_id"], data["correlation_id"], data["purpose"], TerminalBuffer(buffer["first_sequence"], buffer["last_sequence"], buffer["retained_bytes"], buffer["dropped_bytes"], buffer["maximum_bytes"], BufferTruncation(buffer["truncation"])), data["lease_id"], data["current_command_id"], data["policy_version"], datetime.fromisoformat(data["created_at"]), datetime.fromisoformat(data["last_activity_at"]), datetime.fromisoformat(data["expires_at"]), data["output_ref"] or None,
        )


__all__ = ["TerminalPersistenceJournal"]
