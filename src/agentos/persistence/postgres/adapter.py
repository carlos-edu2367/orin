from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import secrets
from typing import Callable, Mapping

from sqlalchemy import func, or_, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from agentos.events.models import DataClassification

from agentos.persistence.models import (
    AuthorizedRead,
    AuthorizedRecord,
    AuthorizedRecordPage,
    AuthorizedScan,
    CommitState,
    InspectCommit,
    NotFound,
    PersistenceErrorCode,
    PersistenceOperationContext,
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
from agentos.persistence.security import classification_allows

from .errors import normalize_database_error
from .schema import (
    persistence_audit,
    persistence_idempotency,
    persistence_outbox,
    persistence_records,
    persistence_clock,
)


CommitHook = Callable[[TransactionRequest], None]
LEGACY_CORRELATION = "__legacy__"


class PersistenceAdapterError(RuntimeError):
    def __init__(self, code: PersistenceErrorCode, retryability: Retryability) -> None:
        self.code = code
        self.retryability = retryability
        super().__init__(f"persistence adapter error: {code.value}")


class PostgresTransactionalPersistence:
    """SQLAlchemy 2 adapter; migration remains an explicit administrative action."""

    def __init__(
        self,
        engine_or_dsn: Engine | Connection | str,
        *,
        session_factory=None,
        commit_hook: CommitHook | None = None,
        engine_options: Mapping[str, object] | None = None,
        cursor_secret: bytes | str | None = None,
    ) -> None:
        if isinstance(engine_or_dsn, str):
            self.engine = create_engine(engine_or_dsn, future=True, **dict(engine_options or {}))
        elif isinstance(engine_or_dsn, Engine):
            self.engine = engine_or_dsn
        else:
            self.engine = engine_or_dsn.engine
        self._Session = session_factory or sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self._commit_hook = commit_hook
        self._cursor_secret = (
            cursor_secret.encode("utf-8")
            if isinstance(cursor_secret, str)
            else cursor_secret
        ) or secrets.token_bytes(32)

    def seed(self, record: AuthorizedRecord) -> None:
        now = datetime.now(timezone.utc)
        with self._Session.begin() as session:
            session.execute(
                persistence_records.insert().values(
                    **self._record_values(record, now=now),
                )
            )
            self._next_revision(session)

    def transact(self, request: TransactionRequest) -> TransactionResult:
        session: Session = self._Session()
        try:
            session.begin()
            self._configure_transaction_options(session, request)
            existing = session.execute(
                select(persistence_idempotency).where(
                    *self._idempotency_scope_filters(request.context),
                    persistence_idempotency.c.idempotency_key == request.idempotency_key,
                ).order_by(self._idempotency_order(request.context))
            ).mappings().first()
            if existing is not None:
                if existing["fingerprint"] != request.fingerprint:
                    session.rollback()
                    return TransactionRejected(
                        PersistenceErrorCode.IDEMPOTENCY_CONFLICT,
                        transaction_id=request.transaction_id,
                    )
                receipt = self._receipt_from_json(existing["receipt"])
                records = self._records_from_json(existing.get("records"), request.context)
                if not records:
                    records = self._legacy_replay_records(session, request, receipt)
                session.rollback()
                return TransactionCommitted(receipt, records, already_applied=True)

            plan = self._plan_transaction(session, request)
            if isinstance(plan, TransactionResult):
                session.rollback()
                return plan
            records, versions, next_revision = plan
            now = datetime.now(timezone.utc)
            for record in records:
                current = session.execute(
                    select(persistence_records.c.version)
                    .where(persistence_records.c.record_ref == str(record.record_ref))
                ).scalar_one_or_none()
                if current is None:
                    session.execute(persistence_records.insert().values(**self._record_values(record, now=now)))
                else:
                    session.execute(
                        persistence_records.update()
                        .where(
                            persistence_records.c.record_ref == str(record.record_ref),
                            persistence_records.c.version == current,
                        )
                        .values(**self._record_values(record, now=now, created_at=None))
                    )

            for audit in request.audit:
                session.execute(
                    persistence_audit.insert().values(
                        audit_ref=audit.audit_ref,
                        transaction_id=request.transaction_id,
                        record_ref=str(audit.record_ref),
                        user_id=request.context.user_id,
                        workspace_id=request.context.workspace_id,
                        agent_id=request.context.agent_id,
                        execution_id=request.context.execution_id,
                        correlation_id=request.context.correlation_id,
                        purpose=request.context.purpose,
                        actor=request.context.actor,
                        decision=audit.decision,
                        resulting_version=audit.resulting_version,
                        fields=as_plain_mapping(audit.fields),
                        created_at=now,
                    )
                )

            for item in request.outbox:
                session.execute(
                    persistence_outbox.insert().values(
                        event_id=str(item.event.event_id),
                        transaction_id=request.transaction_id,
                        source_record_ref=str(item.source_record_ref),
                        expected_source_version=item.expected_source_version,
                        user_id=request.context.user_id,
                        workspace_id=request.context.workspace_id,
                        agent_id=request.context.agent_id,
                        execution_id=request.context.execution_id,
                        correlation_id=request.context.correlation_id,
                        purpose=request.context.purpose,
                        classification=item.event.classification.value,
                        event=self._event_values(item.event),
                        created_at=now,
                    )
                )

            receipt = TransactionReceipt(
                transaction_id=request.transaction_id,
                commit_state=CommitState.COMMITTED,
                record_refs=tuple(record.record_ref for record in records),
                outbox_refs=tuple(item.outbox_ref for item in request.outbox),
                store_revision=next_revision,
                committed_at=now,
            )
            session.execute(
                persistence_idempotency.insert().values(
                    user_id=request.context.user_id,
                    workspace_id=request.context.workspace_id,
                    workspace_scope=request.context.workspace_id or "",
                    agent_id=request.context.agent_id,
                    execution_id=request.context.execution_id,
                    correlation_id=request.context.correlation_id,
                    purpose=request.context.purpose,
                    actor=request.context.actor,
                    idempotency_key=request.idempotency_key,
                    fingerprint=request.fingerprint,
                    transaction_id=request.transaction_id,
                    commit_state=receipt.commit_state.value,
                    receipt=self._receipt_values(receipt),
                    records=self._records_values(records),
                    store_revision=next_revision,
                    created_at=now,
                )
            )
            try:
                session.flush()
            except IntegrityError as error:
                session.rollback()
                replay = self._resolve_idempotency_race(request)
                if replay is not None:
                    return replay
                normalized = normalize_database_error(error)
                code = (
                    PersistenceErrorCode.DUPLICATE_OUTBOX_EVENT
                    if "outbox" in str(error).lower() and "event" in str(error).lower()
                    else normalized.code
                )
                return TransactionRejected(code, normalized.retryability, request.transaction_id)
            except SQLAlchemyError as error:
                session.rollback()
                normalized = normalize_database_error(error)
                return TransactionRejected(normalized.code, normalized.retryability, request.transaction_id)

            try:
                session.commit()
            except Exception:
                session.rollback()
                return TransactionIndeterminate(request.transaction_id)
            if self._commit_hook is not None:
                try:
                    self._commit_hook(request)
                except Exception:
                    return TransactionIndeterminate(request.transaction_id)
            return TransactionCommitted(receipt, tuple(records))
        except SQLAlchemyError as error:
            session.rollback()
            normalized = normalize_database_error(error)
            return TransactionRejected(normalized.code, normalized.retryability, request.transaction_id)
        finally:
            session.close()

    def _resolve_idempotency_race(self, request: TransactionRequest) -> TransactionResult | None:
        with self._Session() as session:
            row = session.execute(
                select(persistence_idempotency).where(
                    *self._idempotency_scope_filters(request.context),
                    persistence_idempotency.c.idempotency_key == request.idempotency_key,
                ).order_by(self._idempotency_order(request.context))
            ).mappings().first()
            if row is None:
                return None
            if row["fingerprint"] != request.fingerprint:
                return TransactionRejected(
                    PersistenceErrorCode.IDEMPOTENCY_CONFLICT,
                    transaction_id=request.transaction_id,
                )
            receipt = self._receipt_from_json(row["receipt"])
            records = self._records_from_json(row.get("records"), request.context)
            if not records:
                records = self._legacy_replay_records(session, request, receipt)
            return TransactionCommitted(receipt, records, already_applied=True)

    def read(self, query: AuthorizedRead) -> AuthorizedRecord | NotFound:
        with self._Session() as session:
            try:
                return self._read_in_session(session, query)
            except SQLAlchemyError as error:
                normalized = normalize_database_error(error)
                raise PersistenceAdapterError(normalized.code, normalized.retryability) from None

    def scan(self, query: AuthorizedScan) -> AuthorizedRecordPage:
        fingerprint = self._scan_fingerprint(query)
        with self._Session() as session:
            revision = self._current_revision(session)
            last_ref = None
            if query.page.cursor is not None:
                cursor = self._decode_cursor(query.page.cursor)
                if cursor["fingerprint"] != fingerprint or int(cursor["revision"]) != revision:
                    raise ValueError("invalid persistence cursor")
                last_ref = str(cursor["last_ref"])
            stmt = select(persistence_records).where(
                persistence_records.c.record_type == query.record_type,
                *self._scope_filters(query.context),
                persistence_records.c.classification.in_(
                    [item.value for item in DataClassification if classification_allows(query.classification_ceiling, item)]
                ),
            )
            if last_ref is not None:
                stmt = stmt.where(persistence_records.c.record_ref > last_ref)
            for key, value in query.filters.items():
                stmt = stmt.where(self._json_filter(persistence_records.c.data, key, value))
            rows = session.execute(
                stmt.order_by(persistence_records.c.record_ref).limit(query.page.limit + 1)
            ).mappings().all()
            page_items = tuple(self._row_to_record(row) for row in rows[: query.page.limit])
            next_cursor = None
            if len(rows) > query.page.limit:
                next_cursor = self._encode_cursor(
                    {"fingerprint": fingerprint, "revision": revision, "last_ref": str(page_items[-1].record_ref)}
                )
            return AuthorizedRecordPage(page_items, next_cursor, revision)

    def inspect_commit(self, query: InspectCommit) -> TransactionReceipt:
        try:
            with self._Session() as session:
                filters = [
                    *self._idempotency_scope_filters(query.context),
                    persistence_idempotency.c.idempotency_key == query.idempotency_key,
                ]
                if query.transaction_id is not None:
                    filters.append(persistence_idempotency.c.transaction_id == query.transaction_id)
                row = session.execute(
                    select(persistence_idempotency).where(*filters).order_by(
                        self._idempotency_order(query.context)
                    )
                ).mappings().first()
                if row is None:
                    return TransactionReceipt(
                        transaction_id=query.transaction_id or "inspection:unknown",
                        commit_state=CommitState.NOT_COMMITTED,
                        record_refs=(),
                        outbox_refs=(),
                        store_revision=self._current_revision(session),
                        committed_at=None,
                    )
                receipt = self._receipt_from_json(row["receipt"])
                return replace(
                    receipt,
                    fingerprint=str(row["fingerprint"]),
                    records=self._records_from_json(row.get("records"), query.context),
                )
        except SQLAlchemyError:
            return TransactionReceipt(
                transaction_id=query.transaction_id,
                commit_state=CommitState.UNKNOWN,
                record_refs=(),
                outbox_refs=(),
                store_revision=0,
                committed_at=None,
            )

    def _plan_transaction(self, session: Session, request: TransactionRequest):
        if request.options.read_only and (request.changes or request.audit or request.outbox):
            return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST, transaction_id=request.transaction_id)
        expected_by_ref = {str(item.record_ref): item.version for item in request.expected_versions}
        if len(expected_by_ref) != len(request.expected_versions):
            return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST, transaction_id=request.transaction_id)
        versions: dict[str, int] = {}
        records: list[AuthorizedRecord] = []
        for change in request.changes:
            ref = str(change.record_ref)
            if change.expected_version != expected_by_ref.get(ref):
                return TransactionRejected(PersistenceErrorCode.INVALID_REQUEST, transaction_id=request.transaction_id)
            row = session.execute(
                select(persistence_records).where(persistence_records.c.record_ref == ref).with_for_update()
            ).mappings().first()
            if row is None:
                if change.expected_version is not None:
                    return TransactionConflicted((VersionConflict(change.record_ref, change.expected_version, None),))
                version = 1
            else:
                current = self._row_to_record(row)
                if not self._same_scope(current.context, request.context):
                    return TransactionRejected(PersistenceErrorCode.UNAUTHORIZED, transaction_id=request.transaction_id)
                if current.record_type != change.record_type:
                    return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION, transaction_id=request.transaction_id)
                if change.expected_version != current.version:
                    return TransactionConflicted((VersionConflict(change.record_ref, change.expected_version, current.version),))
                version = current.version + 1
            versions[ref] = version
            records.append(
                AuthorizedRecord(
                    record_ref=change.record_ref,
                    record_type=change.record_type,
                    version=version,
                    context=request.context,
                    classification=change.classification,
                    data=change.data,
                )
            )

        for item in request.outbox:
            event_id = str(item.event.event_id)
            existing = session.execute(
                select(persistence_outbox.c.event_id).where(persistence_outbox.c.event_id == event_id)
            ).scalar_one_or_none()
            if existing is not None:
                return TransactionRejected(PersistenceErrorCode.DUPLICATE_OUTBOX_EVENT, transaction_id=request.transaction_id)
            if versions.get(str(item.source_record_ref)) != item.expected_source_version:
                return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION, transaction_id=request.transaction_id)
            event = item.event
            if (
                event.user_id != request.context.user_id
                or event.workspace_id != request.context.workspace_id
                or event.agent_id != request.context.agent_id
                or event.execution_id != request.context.execution_id
                or event.correlation_id != request.context.correlation_id
                or event.sequence != item.expected_source_version
            ):
                return TransactionRejected(PersistenceErrorCode.UNAUTHORIZED, transaction_id=request.transaction_id)

        audit_refs = set()
        for audit in request.audit:
            if audit.audit_ref in audit_refs or str(audit.record_ref) not in versions:
                return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION, transaction_id=request.transaction_id)
            audit_refs.add(audit.audit_ref)
            if audit.resulting_version != versions[str(audit.record_ref)]:
                return TransactionRejected(PersistenceErrorCode.CONSTRAINT_VIOLATION, transaction_id=request.transaction_id)

        return tuple(records), versions, self._next_revision(session)

    def _read_in_session(self, session: Session, query: AuthorizedRead):
        stmt = select(persistence_records).where(
            persistence_records.c.record_ref == str(query.record_ref),
            persistence_records.c.record_type == query.record_type,
            *self._scope_filters(query.context),
            persistence_records.c.classification.in_(
                [item.value for item in DataClassification if classification_allows(query.classification_ceiling, item)]
            ),
        )
        row = session.execute(stmt).mappings().first()
        return NotFound() if row is None else self._row_to_record(row)

    @staticmethod
    def _same_scope(left: PersistenceOperationContext, right: PersistenceOperationContext) -> bool:
        return left.scope_key() == right.scope_key()

    @staticmethod
    def _scope_filters(context: PersistenceOperationContext):
        workspace = (
            persistence_records.c.workspace_id.is_(None)
            if context.workspace_id is None
            else persistence_records.c.workspace_id == context.workspace_id
        )
        return (
            persistence_records.c.user_id == context.user_id,
            workspace,
            persistence_records.c.agent_id == context.agent_id,
            persistence_records.c.execution_id == context.execution_id,
            persistence_records.c.correlation_id == context.correlation_id,
            persistence_records.c.purpose == context.purpose,
            persistence_records.c.actor == context.actor,
        )

    @staticmethod
    def _idempotency_scope_filters(context: PersistenceOperationContext):
        return (
            persistence_idempotency.c.user_id == context.user_id,
            persistence_idempotency.c.workspace_scope == (context.workspace_id or ""),
            persistence_idempotency.c.agent_id == context.agent_id,
            persistence_idempotency.c.execution_id == context.execution_id,
            or_(
                persistence_idempotency.c.correlation_id == context.correlation_id,
                persistence_idempotency.c.correlation_id == LEGACY_CORRELATION,
            ),
            persistence_idempotency.c.purpose == context.purpose,
            persistence_idempotency.c.actor == context.actor,
        )

    @staticmethod
    def _idempotency_order(context: PersistenceOperationContext):
        return (persistence_idempotency.c.correlation_id == context.correlation_id).desc()

    @staticmethod
    def _record_values(record: AuthorizedRecord, *, now: datetime, created_at: datetime | None = ...):
        values = {
            "record_ref": str(record.record_ref),
            "record_type": record.record_type,
            "version": record.version,
            "user_id": record.context.user_id,
            "workspace_id": record.context.workspace_id,
            "agent_id": record.context.agent_id,
            "execution_id": record.context.execution_id,
            "correlation_id": record.context.correlation_id,
            "purpose": record.context.purpose,
            "actor": record.context.actor,
            "classification": record.classification.value,
            "data": as_plain_mapping(record.data),
            "updated_at": now,
        }
        if created_at is not None:
            values["created_at"] = now
        return values

    @staticmethod
    def _records_values(records: tuple[AuthorizedRecord, ...] | list[AuthorizedRecord]) -> list[dict[str, object]]:
        return [
            {
                "record_ref": str(record.record_ref),
                "record_type": record.record_type,
                "version": record.version,
                "user_id": record.context.user_id,
                "workspace_id": record.context.workspace_id,
                "agent_id": record.context.agent_id,
                "execution_id": record.context.execution_id,
                "correlation_id": record.context.correlation_id,
                "purpose": record.context.purpose,
                "actor": record.context.actor,
                "classification": record.classification.value,
                "data": as_plain_mapping(record.data),
            }
            for record in records
        ]

    @staticmethod
    def _records_from_json(value, context: PersistenceOperationContext) -> tuple[AuthorizedRecord, ...]:
        if not value:
            return ()
        records = []
        for item in value:
            records.append(
                AuthorizedRecord(
                    record_ref=RecordReference(str(item["record_ref"])),
                    record_type=str(item["record_type"]),
                    version=int(item["version"]),
                    context=PersistenceOperationContext(
                        user_id=str(item["user_id"]),
                        workspace_id=item.get("workspace_id"),
                        agent_id=str(item["agent_id"]),
                        execution_id=str(item["execution_id"]),
                        correlation_id=str(item["correlation_id"]),
                        purpose=str(item["purpose"]),
                        actor=str(item["actor"]),
                    ),
                    classification=DataClassification(str(item["classification"])),
                    data=item["data"],
                )
            )
        if any(not PostgresTransactionalPersistence._same_scope(record.context, context) for record in records):
            return ()
        return tuple(records)

    def _legacy_replay_records(self, session: Session, request: TransactionRequest, receipt: TransactionReceipt):
        records = []
        for reference in receipt.record_refs:
            record = self._read_in_session(
                session,
                AuthorizedRead(
                    context=request.context,
                    record_ref=reference,
                    record_type="execution",
                    classification_ceiling=DataClassification.INTERNAL,
                ),
            )
            if not isinstance(record, NotFound):
                records.append(record)
        return tuple(records)

    def _configure_transaction_options(self, session: Session, request: TransactionRequest) -> None:
        if self.engine.dialect.name != "postgresql":
            return
        isolation = {
            "READ_COMMITTED": "READ COMMITTED",
            "REPEATABLE_READ": "REPEATABLE READ",
            "SERIALIZABLE": "SERIALIZABLE",
        }[request.options.isolation.value]
        if request.options.consistency.value == "EVENTUAL":
            isolation = "READ COMMITTED"
        session.connection(execution_options={"isolation_level": isolation})
        if request.options.read_only:
            session.execute(text("SET TRANSACTION READ ONLY"))
        timeout_ms = max(1, int(request.options.timeout.total_seconds() * 1000))
        session.execute(
            select(func.set_config("statement_timeout", str(timeout_ms), True))
        )

    @staticmethod
    def _json_filter(column, key: str, value: object):
        expression = column[key]
        if value is None:
            return expression.is_(None)
        if isinstance(value, bool):
            return expression.as_boolean() == value
        if isinstance(value, int) and not isinstance(value, bool):
            return expression.as_integer() == value
        if isinstance(value, float):
            return expression.as_float() == value
        if isinstance(value, str):
            return expression.as_string() == value
        raise ValueError("scan filters must contain scalar values")

    def _encode_cursor(self, payload: Mapping[str, object]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._cursor_secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + b"." + signature).decode("ascii").rstrip("=")

    def _decode_cursor(self, value: str) -> dict[str, object]:
        try:
            padded = value + "=" * (-len(value) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            body, signature = raw.rsplit(b".", 1)
            expected = hmac.new(self._cursor_secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"fingerprint", "revision", "last_ref"}:
                raise ValueError
            return payload
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("invalid persistence cursor") from None

    @staticmethod
    def _row_to_record(row) -> AuthorizedRecord:
        mapping = row if isinstance(row, Mapping) else row._mapping
        context = PersistenceOperationContext(
            user_id=mapping["user_id"],
            workspace_id=mapping["workspace_id"],
            agent_id=mapping["agent_id"],
            execution_id=mapping["execution_id"],
            correlation_id=mapping["correlation_id"],
            purpose=mapping["purpose"],
            actor=mapping["actor"],
        )
        return AuthorizedRecord(
            record_ref=RecordReference(mapping["record_ref"]),
            record_type=mapping["record_type"],
            version=mapping["version"],
            context=context,
            classification=DataClassification(mapping["classification"]),
            data=mapping["data"],
        )

    @staticmethod
    def _event_values(event) -> dict[str, object]:
        return {
            "event_id": str(event.event_id),
            "event_type": str(event.event_type),
            "event_version": event.event_version,
            "occurred_at": event.occurred_at.isoformat(),
            "source": event.source,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "sequence": event.sequence,
            "user_id": event.user_id,
            "workspace_id": event.workspace_id,
            "agent_id": event.agent_id,
            "execution_id": event.execution_id,
            "classification": event.classification.value,
            "payload": as_plain_mapping(event.payload),
        }

    @staticmethod
    def _receipt_values(receipt: TransactionReceipt) -> dict[str, object]:
        return {
            "transaction_id": receipt.transaction_id,
            "commit_state": receipt.commit_state.value,
            "record_refs": [str(item) for item in receipt.record_refs],
            "outbox_refs": [item.value for item in receipt.outbox_refs],
            "store_revision": receipt.store_revision,
            "committed_at": receipt.committed_at.isoformat() if receipt.committed_at else None,
        }

    @staticmethod
    def _receipt_from_json(value: Mapping[str, object]) -> TransactionReceipt:
        committed_at = value.get("committed_at")
        return TransactionReceipt(
            transaction_id=str(value["transaction_id"]),
            commit_state=CommitState(str(value["commit_state"])),
            record_refs=tuple(RecordReference(str(item)) for item in value.get("record_refs", [])),
            outbox_refs=tuple(__import__("agentos.persistence", fromlist=["OutboxReference"]).OutboxReference(str(item)) for item in value.get("outbox_refs", [])),
            store_revision=int(value["store_revision"]),
            committed_at=datetime.fromisoformat(str(committed_at)) if committed_at else None,
        )

    def _current_revision(self, session: Session) -> int:
        value = session.execute(
            select(persistence_clock.c.revision).where(persistence_clock.c.id == 1)
        ).scalar_one_or_none()
        return int(value or 0)

    def _next_revision(self, session: Session) -> int:
        current = session.execute(
            select(persistence_clock.c.revision)
            .where(persistence_clock.c.id == 1)
            .with_for_update()
        ).scalar_one()
        next_revision = int(current) + 1
        session.execute(
            persistence_clock.update()
            .where(persistence_clock.c.id == 1)
            .values(revision=next_revision)
        )
        return next_revision

    def _scan_fingerprint(self, query: AuthorizedScan) -> str:
        payload = {
            "scope": query.context.scope_key(),
            "record_type": query.record_type,
            "filters": as_plain_mapping(query.filters),
            "classification": query.classification_ceiling.value,
            "limit": query.page.limit,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["PersistenceAdapterError", "PostgresTransactionalPersistence"]
