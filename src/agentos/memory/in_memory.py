from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from itertools import count
from threading import RLock

from agentos.events.models import DataClassification, EventEnvelope
from agentos.events.security import clearance_allows

from .models import (
    ApplyMemoryRetention,
    AuthorizedMemory,
    BoundedMemoryContent,
    ConsolidateMemory,
    MemoryConsolidationReceipt,
    GetMemory,
    InvalidateMemory,
    MemoryArtifactReference,
    MemoryAuditRecord,
    MemoryCommitFailure,
    MemoryCommitChange,
    MemoryCommitRequest,
    MemoryCommitResult,
    MemoryIdempotencyConflict,
    MemoryOperation,
    MemoryOperationContext,
    MemoryMatch,
    MemoryMatchReason,
    MemoryRecord,
    MemoryReference,
    MemoryRevision,
    MemorySearchResult,
    MemoryScope,
    MemoryStatus,
    MemoryVersionConflict,
    MemoryWriteReceipt,
    RetentionReceipt,
    SaveMemory,
    SearchMemory,
)
from .security import (
    InMemoryMemoryAuthorizationPolicy,
    fingerprint_command,
    validate_memory_content,
    validate_provenance,
    validate_scope,
)


_EVENT_PAYLOAD_KEYS = {
    "memory_id",
    "memory_ids",
    "version",
    "versions",
    "scope",
    "kind",
    "status",
    "outcome",
    "reason",
    "result_count",
    "truncated",
    "source_memory_ids",
    "source_versions",
    "retention_run_id",
    "consolidation_id",
    "evaluated_count",
    "expired_count",
    "invalidated_count",
    "retained_count",
    "classification",
    "purpose",
    "operation",
}


class InMemoryMemoryStore:
    """Bounded reference store with conceptual atomic state/audit/outbox commits."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._revisions: list[object] = []
        self._audit_log: list[object] = []
        self._outbox: list[EventEnvelope] = []
        self._idempotency: dict[tuple[str, ...], tuple[str, MemoryCommitResult]] = {}
        self._event_sequences: defaultdict[str, int] = defaultdict(int)
        self._lock = RLock()

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    @property
    def revisions(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._revisions)

    @property
    def audit_log(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._audit_log)

    @property
    def outbox(self) -> tuple[EventEnvelope, ...]:
        with self._lock:
            return tuple(self._outbox)

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._lock:
            return self._records.get(str(memory_id))

    def list_records(self) -> tuple[MemoryRecord, ...]:
        return self.records

    def next_event_sequence(self, execution_id: str) -> int:
        with self._lock:
            return self._event_sequences[str(execution_id)] + 1

    def lookup_idempotency(self, context, operation: str, key: str, fingerprint: str):
        with self._lock:
            lookup_key = (
                str(context.user_id),
                str(context.workspace_id),
                str(context.agent_id),
                str(context.execution_id),
                str(context.purpose),
                str(operation),
                str(key),
            )
            previous = self._idempotency.get(lookup_key)
            if previous is None:
                return None
            previous_fingerprint, previous_result = previous
            if previous_fingerprint != fingerprint:
                raise MemoryIdempotencyConflict()
            return previous_result

    def commit(self, request: MemoryCommitRequest) -> MemoryCommitResult:
        with self._lock:
            key = (
                str(request.context.user_id),
                str(request.context.workspace_id),
                str(request.context.agent_id),
                str(request.context.execution_id),
                str(request.context.purpose),
                request.operation.value,
                str(request.idempotency_key),
            )
            previous = self._idempotency.get(key)
            if previous is not None:
                previous_fingerprint, previous_result = previous
                if previous_fingerprint != request.fingerprint:
                    raise MemoryIdempotencyConflict()
                return MemoryCommitResult(
                    applied=False,
                    already_applied=True,
                    result=previous_result.result,
                    event_id=previous_result.event_id,
                )

            self._validate_request(request)
            for change in request.changes:
                current = self._records.get(str(change.record.memory_id))
                expected = change.expected_version
                actual = current.version if current is not None else None
                if expected != actual:
                    if current is None and expected is None:
                        pass
                    else:
                        raise MemoryVersionConflict()
                if current is not None:
                    if current.status is not MemoryStatus.ACTIVE and change.record.status is MemoryStatus.ACTIVE:
                        raise MemoryVersionConflict("TOMBSTONE_RESURRECTION")
                    if int(change.record.version) != int(current.version) + 1:
                        raise MemoryVersionConflict()
                elif int(change.record.version) != 1:
                    raise MemoryVersionConflict()
                if int(change.revision.version) != int(change.record.version):
                    raise MemoryCommitFailure("REVISION_MISMATCH")

            result = MemoryCommitResult(
                applied=True,
                already_applied=False,
                result=request.result,
                event_id=request.event.event_id,
            )
            for change in request.changes:
                self._records[str(change.record.memory_id)] = change.record
                self._revisions.append(change.revision)
            self._audit_log.append(request.audit)
            events = (request.event, *request.additional_events)
            self._outbox.extend(events)
            self._event_sequences[str(request.context.execution_id)] = events[-1].sequence or 0
            self._idempotency[key] = (request.fingerprint, result)
            return result

    def _validate_request(self, request: MemoryCommitRequest) -> None:
        event = request.event
        events = (request.event, *request.additional_events)
        context = request.context
        if event.event_id != request.audit.event_id:
            raise MemoryCommitFailure("AUDIT_EVENT_MISMATCH")
        if (
            event.user_id != context.user_id
            or event.workspace_id != context.workspace_id
            or event.agent_id != context.agent_id
            or event.execution_id != context.execution_id
            or event.correlation_id != context.correlation_id
        ):
            raise MemoryCommitFailure("EVENT_CONTEXT_MISMATCH")
        expected_sequence = self.next_event_sequence(str(context.execution_id))
        existing_event_ids = {item.event_id for item in self._outbox}
        for candidate in events:
            if (
                candidate.user_id != context.user_id
                or candidate.workspace_id != context.workspace_id
                or candidate.agent_id != context.agent_id
                or candidate.execution_id != context.execution_id
                or candidate.correlation_id != context.correlation_id
            ):
                raise MemoryCommitFailure("EVENT_CONTEXT_MISMATCH")
            if candidate.sequence != expected_sequence:
                raise MemoryCommitFailure("EVENT_SEQUENCE_CONFLICT")
            if candidate.event_id in existing_event_ids:
                raise MemoryCommitFailure("EVENT_DUPLICATE")
            if not candidate.event_type.startswith("Memory"):
                raise MemoryCommitFailure("EVENT_TYPE_INVALID")
            if any(key not in _EVENT_PAYLOAD_KEYS for key in candidate.payload):
                raise MemoryCommitFailure("EVENT_PAYLOAD_INVALID")
            expected_sequence += 1


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class InMemoryMemorySearchAdapter:
    def rank(self, records: tuple[MemoryRecord, ...], query: SearchMemory) -> tuple[MemoryMatch, ...]:
        terms = tuple(term for term in query.query.text.lower().split() if term)
        ranked: list[tuple[float, float, str, MemoryMatch]] = []
        for record in records:
            if not isinstance(record.content, BoundedMemoryContent):
                continue
            text = record.content.value
            lowered = text.lower()
            hits = sum(lowered.count(term) for term in terms)
            if hits == 0:
                continue
            relevance = hits / max(1, len(terms))
            excerpt = text[:512]
            ranked.append(
                (
                    relevance,
                    record.created_at.timestamp(),
                    str(record.memory_id),
                    MemoryMatch(
                        memory_ref=MemoryReference(
                            memory_id=record.memory_id,
                            version=record.version,
                            user_id=record.user_id,
                            workspace_id=record.workspace_id,
                            permitted_agent_id=query.context.agent_id,
                            authorization_ref=f"owner:{record.owner_agent_id or query.context.agent_id}",
                            purpose=query.context.purpose,
                            expires_at=record.expires_at,
                            integrity_ref=record.provenance.integrity_ref or "integrity:memory",
                        ),
                        version=record.version,
                        kind=record.kind,
                        scope=record.scope,
                        excerpt=excerpt,
                        relevance=relevance,
                        match_reasons=(MemoryMatchReason.TERM_MATCH, MemoryMatchReason.FILTER_MATCH),
                        provenance=record.provenance,
                        classification=record.classification,
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        output: list[MemoryMatch] = []
        used_units = 0
        for _, _, _, match in ranked:
            if len(output) >= query.maximum_results:
                break
            excerpt = match.excerpt or ""
            remaining = query.maximum_content_units - used_units
            if remaining <= 0:
                break
            if len(excerpt) > remaining:
                match = replace(match, excerpt=excerpt[:remaining])
            output.append(match)
            used_units += len(match.excerpt or "")
        return tuple(output)


class InMemoryMemoryManager:
    """Reference RFC 301 manager over injected public ports."""

    def __init__(self, *, store=None, authorization=None, clock=None, search=None, policy_version: str = "memory-policy:1") -> None:
        self.store = store or InMemoryMemoryStore()
        self.authorization = authorization or InMemoryMemoryAuthorizationPolicy()
        self.clock = clock or _SystemClock()
        self.search_adapter = search or InMemoryMemorySearchAdapter()
        self.policy_version = policy_version
        self._memory_ids = count(1)
        self._operation_ids = count(1)
        self._lock = RLock()

    def save(self, command: SaveMemory) -> MemoryWriteReceipt:
        now = self.clock.now()
        fingerprint = fingerprint_command(command)
        previous = self.store.lookup_idempotency(command.context, MemoryOperation.SAVE.value, str(command.idempotency_key), fingerprint)
        if previous is not None:
            return replace(previous.result, already_applied=True)
        validate_memory_content(command.content)
        validate_provenance(command.provenance)
        owner_agent_id = str(command.context.agent_id) if command.scope is MemoryScope.PRIVATE else None
        validate_scope(
            command.scope,
            user_id=str(command.context.user_id),
            workspace_id=str(command.context.workspace_id) if command.context.workspace_id is not None else None,
            owner_agent_id=owner_agent_id,
        )
        current = None
        if command.memory_ref is not None:
            current = self.store.get(str(command.memory_ref.memory_id))
            if current is None:
                raise MemoryVersionConflict()
            try:
                self.authorization.authorize(
                    command.context,
                    current,
                    operation="WRITE",
                    reference=command.memory_ref,
                    classification_ceiling=command.context.classification_ceiling,
                    now=now,
                )
            except Exception as error:
                self._record_access_denied(command.context, command.idempotency_key, command.scope, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.SAVE)
                raise
            if command.expected_version != current.version:
                raise MemoryVersionConflict()
            if current.status is not MemoryStatus.ACTIVE:
                raise MemoryVersionConflict("TOMBSTONE_RESURRECTION")
            record = replace(
                current,
                content=self._content(command.content),
                kind=command.kind,
                provenance=command.provenance,
                classification=command.classification,
                retention_policy_ref=command.retention_policy_ref,
                expires_at=command.expires_at,
                version=int(current.version) + 1,
                status=MemoryStatus.ACTIVE,
                invalidated_at=None,
                superseded_by=None,
            )
            previous_version = int(current.version)
        else:
            memory_id = f"memory:{next(self._memory_ids)}"
            record = MemoryRecord(
                memory_id=memory_id,
                user_id=command.context.user_id,
                workspace_id=command.context.workspace_id if command.scope is not MemoryScope.USER else None,
                owner_agent_id=owner_agent_id,
                scope=command.scope,
                base_scope=command.scope,
                kind=command.kind,
                content=self._content(command.content),
                provenance=command.provenance,
                classification=command.classification,
                retention_policy_ref=command.retention_policy_ref,
                status=MemoryStatus.ACTIVE,
                version=1,
                created_by=command.context.actor,
                created_execution_id=command.context.execution_id,
                correlation_id=command.context.correlation_id,
                created_at=now,
                valid_from=now,
                expires_at=command.expires_at,
            )
            try:
                self.authorization.authorize(
                    command.context,
                    record,
                    operation="WRITE",
                    classification_ceiling=command.context.classification_ceiling,
                    now=now,
                )
            except Exception as error:
                self._record_access_denied(command.context, command.idempotency_key, command.scope, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.SAVE)
                raise
            previous_version = None

        receipt = MemoryWriteReceipt(
            memory_id=record.memory_id,
            version=record.version,
            status=record.status,
            correlation_id=command.context.correlation_id,
            event_id=f"memory:{record.memory_id}:v{record.version}:saved",
        )
        return self._commit_change(
            operation=MemoryOperation.SAVE,
            context=command.context,
            key=command.idempotency_key,
            fingerprint=fingerprint,
            record=record,
            expected_version=previous_version,
            reason="saved",
            event_type="MemorySaved",
            payload={
                "memory_id": str(record.memory_id),
                "version": int(record.version),
                "scope": record.scope.value,
                "kind": record.kind.value,
                "status": record.status.value,
                "classification": record.classification.value,
            },
            receipt=receipt,
        )

    def get(self, query: GetMemory) -> AuthorizedMemory:
        now = self.clock.now()
        record = self.store.get(str(query.memory_ref.memory_id))
        try:
            self.authorization.authorize(
                query.context,
                record,
                operation="READ",
                reference=query.memory_ref,
                classification_ceiling=query.classification_ceiling,
                now=now,
            )
        except Exception as error:
            self._record_access_denied(query.context, f"read:{query.memory_ref.memory_id}:{query.memory_ref.version}", None, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.READ)
            raise
        if record is None:
            raise MemoryCommitFailure("READ_UNAVAILABLE")
        authorized = AuthorizedMemory(
            memory_ref=query.memory_ref,
            version=record.version,
            content=record.content,
            provenance=record.provenance,
            classification=record.classification,
            status=record.status,
            authorized_scope=record.scope,
            purpose=query.context.purpose,
            policy_version=self.policy_version,
            retrieved_at=now,
            correlation_id=query.context.correlation_id,
        )
        self._record_fact(
            operation=MemoryOperation.READ,
            context=query.context,
            key=f"read:{record.memory_id}:{record.version}:{query.context.correlation_id}",
            fingerprint=fingerprint_command(query),
            event_type="MemoryRead",
            payload={"memory_id": str(record.memory_id), "version": int(record.version), "scope": record.scope.value, "classification": record.classification.value},
            memory_ids=(str(record.memory_id),),
            versions=(int(record.version),),
            scope=record.scope,
            reason="authorized",
        )
        return authorized

    def search(self, query: SearchMemory) -> MemorySearchResult:
        now = self.clock.now()
        fingerprint = fingerprint_command(query)
        authorized_records: list[MemoryRecord] = []
        reference_by_id: dict[str, MemoryReference] = {}
        for record in self.store.list_records():
            if record.scope not in query.allowed_scopes:
                continue
            if not self._passes_filters(record, query):
                continue
            reference = MemoryReference(
                memory_id=record.memory_id,
                version=record.version,
                user_id=record.user_id,
                workspace_id=record.workspace_id,
                permitted_agent_id=query.context.agent_id,
                authorization_ref=(query.grant_refs[0] if query.grant_refs else f"owner:{record.owner_agent_id or query.context.agent_id}"),
                purpose=query.context.purpose,
                expires_at=record.expires_at,
                integrity_ref=record.provenance.integrity_ref or "integrity:memory",
            )
            try:
                self.authorization.authorize(
                    query.context,
                    record,
                    operation="SEARCH",
                    reference=reference,
                    classification_ceiling=query.classification_ceiling,
                    grant_refs=query.grant_refs,
                    now=now,
                )
            except Exception:
                continue
            authorized_records.append(record)
            reference_by_id[str(record.memory_id)] = reference
        matches = self.search_adapter.rank(tuple(authorized_records), query)
        matches = tuple(
            replace(match, memory_ref=reference_by_id.get(str(match.memory_ref.memory_id), match.memory_ref))
            for match in matches
        )
        truncated = len(matches) >= query.maximum_results and len(authorized_records) > len(matches)
        result = MemorySearchResult(
            matches=matches,
            applied_scope=query.allowed_scopes,
            policy_version=self.policy_version,
            truncated=truncated,
            correlation_id=query.context.correlation_id,
        )
        self._record_fact(
            operation=MemoryOperation.SEARCH,
            context=query.context,
            key=f"search:{query.context.correlation_id}:{fingerprint}",
            fingerprint=fingerprint,
            event_type="MemorySearched",
            payload={"result_count": len(matches), "truncated": truncated, "scope": query.allowed_scopes[0].value, "classification": query.classification_ceiling.value},
            memory_ids=tuple(str(match.memory_ref.memory_id) for match in matches),
            versions=tuple(int(match.version) for match in matches),
            scope=query.allowed_scopes[0],
            reason="authorized",
        )
        return result

    def consolidate(self, command: ConsolidateMemory) -> MemoryConsolidationReceipt:
        now = self.clock.now()
        fingerprint = fingerprint_command(command)
        previous = self.store.lookup_idempotency(command.context, MemoryOperation.CONSOLIDATE.value, str(command.idempotency_key), fingerprint)
        if previous is not None:
            return replace(previous.result, already_applied=True)
        validate_memory_content(command.content)
        validate_provenance(command.provenance)
        current_sources: list[MemoryRecord] = []
        for source_ref in command.source_refs:
            source = self.store.get(str(source_ref.memory_id))
            try:
                self.authorization.authorize(
                    command.context,
                    source,
                    operation="CONSOLIDATE",
                    reference=source_ref,
                    classification_ceiling=DataClassification.RESTRICTED,
                    now=now,
                )
            except Exception as error:
                self._record_access_denied(command.context, command.idempotency_key, command.target_scope, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.CONSOLIDATE)
                raise
            if source is None or source.scope is not command.target_scope or int(source.version) != int(source_ref.version):
                raise MemoryAccessDenied("CONSOLIDATION_UNAVAILABLE")
            current_sources.append(source)
        owner_agent_id = str(command.context.agent_id) if command.target_scope is MemoryScope.PRIVATE else None
        validate_scope(
            command.target_scope,
            user_id=str(command.context.user_id),
            workspace_id=str(command.context.workspace_id) if command.context.workspace_id is not None else None,
            owner_agent_id=owner_agent_id,
        )
        memory_id = f"memory:{next(self._memory_ids)}"
        strictest = max(
            (source.classification for source in current_sources),
            key=lambda value: {DataClassification.INTERNAL: 1, DataClassification.CONFIDENTIAL: 2, DataClassification.RESTRICTED: 3}[value],
        )
        source_ids = tuple(str(source.memory_id) for source in current_sources)
        lineage = tuple(command.source_refs)
        provenance_refs = tuple(dict.fromkeys((*command.provenance.source_refs, *source_ids)))
        provenance = replace(command.provenance, source_refs=provenance_refs[:32])
        output = MemoryRecord(
            memory_id=memory_id,
            user_id=command.context.user_id,
            workspace_id=command.context.workspace_id if command.target_scope is not MemoryScope.USER else None,
            owner_agent_id=owner_agent_id,
            scope=command.target_scope,
            base_scope=command.target_scope,
            kind=command.target_kind,
            content=self._content(command.content),
            provenance=provenance,
            classification=strictest,
            retention_policy_ref=command.retention_policy_ref,
            status=MemoryStatus.ACTIVE,
            version=1,
            created_by=command.context.actor,
            created_execution_id=command.context.execution_id,
            correlation_id=command.context.correlation_id,
            created_at=now,
            valid_from=now,
            lineage=lineage,
        )
        try:
            self.authorization.authorize(command.context, output, operation="CONSOLIDATE", classification_ceiling=command.context.classification_ceiling, now=now)
        except Exception as error:
            self._record_access_denied(command.context, command.idempotency_key, command.target_scope, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.CONSOLIDATE)
            raise
        consolidation_id = f"consolidation:{next(self._operation_ids)}"
        event_id = f"memory:{consolidation_id}"
        receipt = MemoryConsolidationReceipt(
            consolidation_id=consolidation_id,
            output_memory_id=output.memory_id,
            output_version=output.version,
            source_memory_ids=source_ids,
            source_versions=tuple(int(source.version) for source in current_sources),
            status="CONSOLIDATED",
            execution_id=command.context.execution_id,
            correlation_id=command.context.correlation_id,
            event_id=event_id,
        )
        changes = [
            MemoryCommitChange(
                output,
                None,
                MemoryRevision(
                    memory_id=output.memory_id,
                    version=1,
                    previous_version=None,
                    changed_by=command.context.actor,
                    execution_id=command.context.execution_id,
                    correlation_id=command.context.correlation_id,
                    change_reason="consolidated",
                    changed_at=now,
                ),
            )
        ]
        if command.supersede_sources:
            for source in current_sources:
                superseded = replace(
                    source,
                    version=int(source.version) + 1,
                    status=MemoryStatus.SUPERSEDED,
                    superseded_by=output.memory_id,
                )
                changes.append(
                    MemoryCommitChange(
                        superseded,
                        int(source.version),
                        MemoryRevision(
                            memory_id=superseded.memory_id,
                            version=superseded.version,
                            previous_version=source.version,
                            changed_by=command.context.actor,
                            execution_id=command.context.execution_id,
                            correlation_id=command.context.correlation_id,
                            change_reason="superseded_by_consolidation",
                            changed_at=now,
                        ),
                    )
                )
        committed = self._commit(
            operation=MemoryOperation.CONSOLIDATE,
            context=command.context,
            key=command.idempotency_key,
            fingerprint=fingerprint,
            changes=tuple(changes),
            memory_ids=(str(output.memory_id), *source_ids),
            versions=(1, *tuple(int(source.version) for source in current_sources)),
            scope=output.scope,
            reason="consolidated",
            event_type="MemoryConsolidated",
            payload={
                "memory_id": str(output.memory_id),
                "version": 1,
                "scope": output.scope.value,
                "kind": output.kind.value,
                "source_memory_ids": source_ids,
                "source_versions": tuple(int(source.version) for source in current_sources),
                "classification": output.classification.value,
            },
            result=receipt,
            event_id=event_id,
            additional_event_specs=(
                (
                    ("MemorySuperseded", f"{event_id}:superseded", {
                        "source_memory_ids": source_ids,
                        "source_versions": tuple(int(source.version) for source in current_sources),
                        "scope": output.scope.value,
                        "status": MemoryStatus.SUPERSEDED.value,
                        "classification": output.classification.value,
                    }),
                )
                if command.supersede_sources else ()
            ),
        )
        return replace(committed.result, already_applied=committed.already_applied)

    def _passes_filters(self, record: MemoryRecord, query: SearchMemory) -> bool:
        if record.status is not MemoryStatus.ACTIVE:
            return False
        if not clearance_allows(query.classification_ceiling.value, record.classification.value):
            return False
        for item in query.filters:
            if item.scopes and record.scope not in item.scopes:
                return False
            if item.kinds and record.kind not in item.kinds:
                return False
            if item.statuses and record.status not in item.statuses:
                return False
            if not clearance_allows(item.classification_ceiling.value, record.classification.value):
                return False
            if item.source_kinds and record.provenance.source_kind not in item.source_kinds:
                return False
            if item.authored_by and record.provenance.authored_by not in item.authored_by:
                return False
            if item.source_refs and not set(item.source_refs).intersection(record.provenance.source_refs):
                return False
            if item.created_from and record.created_at < item.created_from:
                return False
            if item.created_to and record.created_at > item.created_to:
                return False
            if item.valid_at and not (record.valid_from <= item.valid_at and (record.expires_at is None or item.valid_at < record.expires_at)):
                return False
            if item.minimum_confidence is not None and (record.provenance.confidence or 0.0) < item.minimum_confidence:
                return False
        return True

    def invalidate(self, command: InvalidateMemory) -> MemoryWriteReceipt:
        now = self.clock.now()
        fingerprint = fingerprint_command(command)
        previous = self.store.lookup_idempotency(command.context, MemoryOperation.INVALIDATE.value, str(command.idempotency_key), fingerprint)
        if previous is not None:
            return replace(previous.result, already_applied=True)
        current = self.store.get(str(command.memory_ref.memory_id))
        try:
            self.authorization.authorize(
                command.context,
                current,
                operation="INVALIDATE",
                reference=command.memory_ref,
                classification_ceiling=DataClassification.RESTRICTED,
                now=now,
            )
        except Exception as error:
            self._record_access_denied(command.context, command.idempotency_key, None, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.INVALIDATE)
            raise
        if current is None:
            raise MemoryCommitFailure("INVALIDATION_UNAVAILABLE")
        if int(command.expected_version) != int(current.version):
            raise MemoryVersionConflict()
        if current.status is not MemoryStatus.ACTIVE:
            raise MemoryVersionConflict("ALREADY_TERMINAL")
        record = replace(
            current,
            version=int(current.version) + 1,
            status=MemoryStatus.INVALIDATED,
            invalidated_at=now,
        )
        receipt = MemoryWriteReceipt(
            memory_id=record.memory_id,
            version=record.version,
            status=record.status,
            correlation_id=command.context.correlation_id,
            event_id=f"memory:{record.memory_id}:v{record.version}:invalidated",
        )
        return self._commit_change(
            operation=MemoryOperation.INVALIDATE,
            context=command.context,
            key=command.idempotency_key,
            fingerprint=fingerprint,
            record=record,
            expected_version=int(current.version),
            reason=command.reason,
            event_type="MemoryInvalidated",
            payload={"memory_id": str(record.memory_id), "version": int(record.version), "scope": record.scope.value, "reason": command.reason, "classification": record.classification.value},
            receipt=receipt,
        )

    def apply_retention(self, command: ApplyMemoryRetention) -> RetentionReceipt:
        now = self.clock.now()
        fingerprint = fingerprint_command(command)
        previous = self.store.lookup_idempotency(command.context, MemoryOperation.RETENTION.value, str(command.idempotency_key), fingerprint)
        if previous is not None:
            return replace(previous.result, already_applied=True)
        changes: list[MemoryCommitChange] = []
        evaluated = expired = retained = invalidated = 0
        memory_ids: list[str] = []
        versions: list[int] = []
        for memory_ref in command.memory_refs:
            current = self.store.get(str(memory_ref.memory_id))
            try:
                self.authorization.authorize(
                    command.context,
                    current,
                    operation="RETENTION",
                    reference=memory_ref,
                    classification_ceiling=DataClassification.RESTRICTED,
                    now=now,
                )
            except Exception as error:
                self._record_access_denied(command.context, command.idempotency_key, command.scope, getattr(error, "code", "ACCESS_DENIED"), operation=MemoryOperation.RETENTION)
                raise
            if current is None or current.scope is not command.scope:
                raise MemoryCommitFailure("RETENTION_UNAVAILABLE")
            evaluated += 1
            memory_ids.append(str(current.memory_id))
            versions.append(int(current.version))
            if current.status is not MemoryStatus.ACTIVE:
                retained += 1
                continue
            if current.expires_at is not None and current.expires_at <= command.policy_cutoff_at:
                record = replace(
                    current,
                    version=int(current.version) + 1,
                    status=MemoryStatus.EXPIRED,
                    invalidated_at=command.policy_cutoff_at,
                )
                revision = MemoryRevision(
                    memory_id=record.memory_id,
                    version=record.version,
                    previous_version=current.version,
                    changed_by=command.context.actor,
                    execution_id=command.context.execution_id,
                    correlation_id=command.context.correlation_id,
                    change_reason="retention_expired",
                    changed_at=now,
                )
                changes.append(MemoryCommitChange(record, int(current.version), revision))
                expired += 1
            else:
                retained += 1
        run_id = f"retention:{next(self._operation_ids)}"
        event_id = f"memory:{run_id}"
        receipt = RetentionReceipt(
            retention_run_id=run_id,
            evaluated_count=evaluated,
            expired_count=expired,
            invalidated_count=invalidated,
            retained_count=retained,
            policy_version=self.policy_version,
            execution_id=command.context.execution_id,
            correlation_id=command.context.correlation_id,
            event_id=event_id,
        )
        result = self._commit(
            operation=MemoryOperation.RETENTION,
            context=command.context,
            key=command.idempotency_key,
            fingerprint=fingerprint,
            changes=tuple(changes),
            memory_ids=tuple(memory_ids),
            versions=tuple(versions),
            scope=command.scope,
            reason="retention_applied",
            event_type="MemoryExpired",
            payload={
                "retention_run_id": run_id,
                "evaluated_count": evaluated,
                "expired_count": expired,
                "retained_count": retained,
                "scope": command.scope.value,
                "classification": DataClassification.RESTRICTED.value,
            },
            result=receipt,
            event_id=event_id,
        )
        return replace(result.result, already_applied=result.already_applied)

    def _commit_change(self, *, record, expected_version, **kwargs):
        receipt = kwargs.pop("receipt")
        revision = MemoryRevision(
            memory_id=record.memory_id,
            version=record.version,
            previous_version=expected_version,
            changed_by=kwargs["context"].actor,
            execution_id=kwargs["context"].execution_id,
            correlation_id=kwargs["context"].correlation_id,
            change_reason=kwargs["reason"],
            changed_at=self.clock.now(),
        )
        result = self._commit(
            **kwargs,
            changes=(MemoryCommitChange(record, expected_version, revision),),
            memory_ids=(str(record.memory_id),),
            versions=(int(record.version),),
            scope=record.scope,
            event_id=receipt.event_id,
            result=receipt,
        )
        return replace(result.result, already_applied=result.already_applied)

    def _commit(self, *, operation, context, key, fingerprint, changes, memory_ids, versions, scope, reason, event_type, payload, result, event_id, additional_event_specs=(), emit_failure=True, outcome="COMMITTED"):
        event = EventEnvelope(
            event_id=event_id,
            event_type=event_type,
            event_version=1,
            occurred_at=self.clock.now(),
            source="memory",
            correlation_id=context.correlation_id,
            causation_id=None,
            sequence=self.store.next_event_sequence(str(context.execution_id)),
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            agent_id=context.agent_id,
            execution_id=context.execution_id,
            classification=DataClassification(payload.get("classification", DataClassification.INTERNAL)),
            payload={**payload, "purpose": context.purpose},
        )
        audit = MemoryAuditRecord(
            audit_id=f"audit:{event_id}",
            operation=operation,
            context=context,
            outcome=outcome,
            memory_ids=tuple(memory_ids),
            versions=tuple(versions),
            scope=scope,
            reason=reason,
            event_id=event_id,
            classification=DataClassification(payload.get("classification", DataClassification.INTERNAL)),
        )
        additional_events = []
        for offset, (additional_type, additional_id, additional_payload) in enumerate(additional_event_specs, start=1):
            additional_events.append(
                EventEnvelope(
                    event_id=additional_id,
                    event_type=additional_type,
                    event_version=1,
                    occurred_at=self.clock.now(),
                    source="memory",
                    correlation_id=context.correlation_id,
                    causation_id=event.event_id,
                    sequence=(event.sequence or 0) + offset,
                    user_id=context.user_id,
                    workspace_id=context.workspace_id,
                    agent_id=context.agent_id,
                    execution_id=context.execution_id,
                    classification=DataClassification(additional_payload.get("classification", DataClassification.INTERNAL)),
                    payload={**additional_payload, "purpose": context.purpose},
                )
            )
        try:
            return self.store.commit(
                MemoryCommitRequest(
                    operation=operation,
                    context=context,
                    idempotency_key=str(key),
                    fingerprint=fingerprint,
                    changes=tuple(changes),
                    audit=audit,
                    event=event,
                    result=result,
                    additional_events=tuple(additional_events),
                )
            )
        except (MemoryCommitFailure, MemoryVersionConflict, MemoryIdempotencyConflict) as error:
            if emit_failure:
                try:
                    self._record_fact(
                        operation=operation,
                        context=context,
                        key=f"failed:{key}",
                        fingerprint=f"failed:{fingerprint}:{getattr(error, 'code', 'COMMIT_FAILED')}",
                        event_type="MemoryOperationFailed",
                        payload={
                            "operation": operation.value,
                            "outcome": "FAILED",
                            "reason": getattr(error, "code", "COMMIT_FAILED"),
                            "scope": scope.value if scope else "UNKNOWN",
                            "classification": DataClassification.INTERNAL.value,
                        },
                        memory_ids=tuple(memory_ids),
                        versions=tuple(versions),
                        scope=scope,
                        reason=getattr(error, "code", "COMMIT_FAILED"),
                        outcome="FAILED",
                    )
                except Exception:
                    pass
            raise

    def _record_fact(self, *, operation, context, key, fingerprint, event_type, payload, memory_ids, versions, scope, reason, outcome="COMMITTED"):
        event_id = f"memory:{operation.value.lower()}:{next(self._operation_ids)}"
        return self._commit(
            operation=operation,
            context=context,
            key=key,
            fingerprint=fingerprint,
            changes=(),
            memory_ids=memory_ids,
            versions=versions,
            scope=scope,
            reason=reason,
            event_type=event_type,
            payload=payload,
            result=None,
            event_id=event_id,
            emit_failure=False,
            outcome=outcome,
        )

    def _record_access_denied(self, context: MemoryOperationContext, key: str, scope: MemoryScope | None, reason: str, *, operation: MemoryOperation):
        try:
            return self._record_fact(
                operation=operation,
                context=context,
                key=f"denied:{key}",
                fingerprint=f"denied:{key}:{reason}",
                event_type="MemoryAccessDenied",
                payload={"outcome": "DENIED", "reason": reason, "scope": scope.value if scope else "UNKNOWN", "classification": DataClassification.INTERNAL.value},
                memory_ids=(),
                versions=(),
                scope=scope,
                reason=reason,
            )
        except Exception:
            return None

    @staticmethod
    def _content(content):
        if isinstance(content, str):
            return BoundedMemoryContent(content)
        if isinstance(content, (BoundedMemoryContent, MemoryArtifactReference)):
            return content
        raise ValueError("content must be bounded text or an opaque artifact reference")


__all__ = ["InMemoryMemoryManager", "InMemoryMemorySearchAdapter", "InMemoryMemoryStore"]
