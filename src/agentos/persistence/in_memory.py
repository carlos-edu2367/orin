from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import secrets

from agentos.events.models import DataClassification

from .models import (
    AuthorizedRead,
    AuthorizedRecord,
    AuthorizedRecordPage,
    AuthorizedScan,
    CommitState,
    InspectCommit,
    NotFound,
    PersistenceErrorCode,
    RecordReference,
    Retryability,
    TransactionCommitted,
    TransactionConflicted,
    TransactionIndeterminate,
    TransactionReceipt,
    TransactionRejected,
    TransactionRequest,
    TransactionResult,
    VersionConflict,
    as_plain_mapping,
)
from .security import classification_allows, scope_matches


class InMemoryTransactionalPersistence:
    """Deterministic reference adapter for the canonical RFC 601 port."""

    def __init__(self) -> None:
        self._records: dict[str, AuthorizedRecord] = {}
        self._idempotency: dict[tuple[tuple[str, ...], str], tuple[str, TransactionReceipt, tuple[AuthorizedRecord, ...]]] = {}
        self._receipts: dict[tuple[tuple[str, ...], str, str], TransactionReceipt] = {}
        self._outbox: dict[str, object] = {}
        self._audit_records: list[object] = []
        self._store_revision = 0
        self._cursor_state: dict[str, tuple[str, int, int]] = {}
        self._next_rejection: PersistenceErrorCode | None = None
        self._next_not_committed = False
        self._next_indeterminate = False

    @property
    def audit_records(self) -> tuple[object, ...]:
        return tuple(self._audit_records)

    def confirmed_outbox(self) -> tuple[object, ...]:
        return tuple(self._outbox.values())

    def seed(self, record: AuthorizedRecord) -> None:
        key = str(record.record_ref)
        if key in self._records:
            raise ValueError("record already seeded")
        self._records[key] = record
        self._store_revision += 1

    def transact(self, request: TransactionRequest) -> TransactionResult:
        scope = request.context.scope_key()
        idempotency_key = (scope, request.idempotency_key)
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            fingerprint, receipt, records = existing
            if fingerprint != request.fingerprint:
                return TransactionRejected(
                    PersistenceErrorCode.IDEMPOTENCY_CONFLICT,
                    transaction_id=request.transaction_id,
                )
            return TransactionCommitted(receipt, records, already_applied=True)

        if self._next_rejection is not None:
            code = self._next_rejection
            self._next_rejection = None
            receipt = self._receipt(request, CommitState.NOT_COMMITTED)
            self._receipts[(scope, request.transaction_id, request.idempotency_key)] = receipt
            return TransactionRejected(
                code,
                Retryability.POLICY_DEPENDENT,
                request.transaction_id,
                receipt,
            )

        validation = self._validate(request)
        if isinstance(validation, TransactionResult):
            return validation

        new_records, versions = validation
        receipt = self._receipt(request, CommitState.NOT_COMMITTED)
        self._receipts[(scope, request.transaction_id, request.idempotency_key)] = receipt
        if self._next_not_committed:
            self._next_not_committed = False
            return TransactionRejected(
                PersistenceErrorCode.CONNECTION,
                Retryability.POLICY_DEPENDENT,
                request.transaction_id,
                receipt,
            )

        self._store_revision += 1
        committed_receipt = replace(
            receipt,
            commit_state=CommitState.COMMITTED,
            store_revision=self._store_revision,
            committed_at=datetime.now(timezone.utc),
        )
        for record in new_records:
            self._records[str(record.record_ref)] = record
        self._audit_records.extend(request.audit)
        for item in request.outbox:
            self._outbox[str(item.event.event_id)] = item
        self._idempotency[idempotency_key] = (
            request.fingerprint,
            committed_receipt,
            tuple(new_records),
        )
        self._receipts[(scope, request.transaction_id, request.idempotency_key)] = committed_receipt
        if self._next_indeterminate:
            self._next_indeterminate = False
            return TransactionIndeterminate(request.transaction_id)
        return TransactionCommitted(committed_receipt, tuple(new_records))

    def read(self, query: AuthorizedRead) -> AuthorizedRecord | NotFound:
        record = self._records.get(str(query.record_ref))
        if record is None or record.record_type != query.record_type:
            return NotFound()
        if not scope_matches(record.context, query.context):
            return NotFound()
        if not classification_allows(query.classification_ceiling, record.classification):
            return NotFound()
        return record

    def scan(self, query: AuthorizedScan) -> AuthorizedRecordPage:
        fingerprint = self._query_fingerprint(query)
        offset = 0
        if query.page.cursor is not None:
            state = self._cursor_state.get(query.page.cursor)
            if state is None or state != (fingerprint, self._store_revision, state[2]):
                raise ValueError("invalid persistence cursor")
            offset = state[2]

        records = [
            record
            for record in self._records.values()
            if record.record_type == query.record_type
            and scope_matches(record.context, query.context)
            and classification_allows(query.classification_ceiling, record.classification)
            and all(record.data.get(key) == value for key, value in query.filters.items())
        ]
        records.sort(key=lambda item: str(item.record_ref))
        page_items = tuple(records[offset : offset + query.page.limit])
        next_cursor = None
        if offset + query.page.limit < len(records):
            next_cursor = f"cursor:{secrets.token_urlsafe(24)}"
            self._cursor_state[next_cursor] = (
                fingerprint,
                self._store_revision,
                offset + query.page.limit,
            )
        return AuthorizedRecordPage(page_items, next_cursor, self._store_revision)

    def inspect_commit(self, query: InspectCommit) -> TransactionReceipt:
        receipt = self._receipts.get(
            (query.context.scope_key(), query.transaction_id, query.idempotency_key)
        )
        if receipt is None:
            return TransactionReceipt(
                transaction_id=query.transaction_id,
                commit_state=CommitState.NOT_COMMITTED,
                record_refs=(),
                outbox_refs=(),
                store_revision=self._store_revision,
                committed_at=None,
            )
        return receipt

    def _lookup_idempotency(self, context, idempotency_key):
        return self._idempotency.get((context.scope_key(), str(idempotency_key)))

    def reject_next(self, code: PersistenceErrorCode = PersistenceErrorCode.CONSTRAINT_VIOLATION) -> None:
        self._next_rejection = code

    def not_committed_next(self) -> None:
        self._next_not_committed = True

    def indeterminate_next(self) -> None:
        self._next_indeterminate = True

    def _validate(self, request: TransactionRequest):
        changes_by_ref: dict[str, object] = {}
        versions: dict[str, int] = {}
        expected_by_ref = {str(item.record_ref): item.version for item in request.expected_versions}
        if len(expected_by_ref) != len(request.expected_versions):
            return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST)
        if request.options.read_only and (request.changes or request.audit or request.outbox):
            return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST)
        for change in request.changes:
            ref = str(change.record_ref)
            if ref in changes_by_ref:
                return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST)
            if change.expected_version != expected_by_ref.get(ref):
                return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST)
            current = self._records.get(ref)
            if current is not None:
                if not scope_matches(current.context, request.context):
                    return TransactionRejected(PersistenceErrorCode.UNAUTHORIZED)
                if current.record_type != change.record_type:
                    return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION)
                if change.expected_version != current.version:
                    return TransactionConflicted(
                        (VersionConflict(change.record_ref, change.expected_version, current.version),)
                    )
                new_version = current.version + 1
            else:
                if change.expected_version is not None:
                    return TransactionConflicted(
                        (VersionConflict(change.record_ref, change.expected_version, None),)
                    )
                new_version = 1
            changes_by_ref[ref] = change
            versions[ref] = new_version

        outbox_ids: set[str] = set()
        for item in request.outbox:
            event_id = str(item.event.event_id)
            if event_id in outbox_ids or event_id in self._outbox:
                return TransactionRejected(PersistenceErrorCode.DUPLICATE_OUTBOX_EVENT)
            outbox_ids.add(event_id)
            source_ref = str(item.source_record_ref)
            if source_ref not in versions or item.expected_source_version != versions[source_ref]:
                return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION)
            event = item.event
            if (
                event.user_id != request.context.user_id
                or event.workspace_id != request.context.workspace_id
                or event.agent_id != request.context.agent_id
                or event.execution_id != request.context.execution_id
                or event.correlation_id != request.context.correlation_id
                or event.sequence != item.expected_source_version
            ):
                return TransactionRejected(PersistenceErrorCode.UNAUTHORIZED)

        for audit in request.audit:
            if str(audit.record_ref) not in versions:
                return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION)
            if audit.resulting_version != versions[str(audit.record_ref)]:
                return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION)

        records = tuple(
            AuthorizedRecord(
                record_ref=change.record_ref,
                record_type=change.record_type,
                version=versions[str(change.record_ref)],
                context=request.context,
                classification=change.classification,
                data=change.data,
            )
            for change in request.changes
        )
        return records, versions

    def _receipt(self, request: TransactionRequest, state: CommitState) -> TransactionReceipt:
        return TransactionReceipt(
            transaction_id=request.transaction_id,
            commit_state=state,
            record_refs=tuple(change.record_ref for change in request.changes),
            outbox_refs=tuple(item.outbox_ref for item in request.outbox),
            store_revision=self._store_revision,
            committed_at=None,
        )

    def _query_fingerprint(self, query: AuthorizedScan) -> str:
        payload = {
            "scope": query.context.scope_key(),
            "actor": query.context.actor,
            "record_type": query.record_type,
            "filters": as_plain_mapping(query.filters),
            "classification": query.classification_ceiling.value,
            "limit": query.page.limit,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


__all__ = ["InMemoryTransactionalPersistence"]
