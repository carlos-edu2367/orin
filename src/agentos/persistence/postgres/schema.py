from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
)


metadata = MetaData()

persistence_records = Table(
    "persistence_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("record_ref", String(255), nullable=False),
    Column("record_type", String(96), nullable=False),
    Column("version", Integer, nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("classification", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("record_ref", name="uq_persistence_records_record_ref"),
)
Index(
    "ix_persistence_records_scope",
    persistence_records.c.user_id,
    persistence_records.c.workspace_id,
    persistence_records.c.agent_id,
    persistence_records.c.execution_id,
    persistence_records.c.record_type,
)

persistence_audit = Table(
    "persistence_audit",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("audit_ref", String(128), nullable=False),
    Column("transaction_id", String(128), nullable=False),
    Column("record_ref", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("decision", String(64), nullable=False),
    Column("resulting_version", Integer, nullable=False),
    Column("fields", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("audit_ref", name="uq_persistence_audit_ref"),
)
Index("ix_persistence_audit_scope", persistence_audit.c.user_id, persistence_audit.c.workspace_id, persistence_audit.c.execution_id)

persistence_outbox = Table(
    "persistence_outbox",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", String(255), nullable=False),
    Column("transaction_id", String(128), nullable=False),
    Column("source_record_ref", String(255), nullable=False),
    Column("expected_source_version", Integer, nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("classification", String(32), nullable=False),
    Column("event", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("event_id", name="uq_persistence_outbox_event_id"),
)
Index("ix_persistence_outbox_pending", persistence_outbox.c.published_at, persistence_outbox.c.created_at)
Index("ix_persistence_outbox_scope", persistence_outbox.c.user_id, persistence_outbox.c.workspace_id, persistence_outbox.c.execution_id)

persistence_idempotency = Table(
    "persistence_idempotency",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("fingerprint", String(128), nullable=False),
    Column("transaction_id", String(128), nullable=False),
    Column("commit_state", String(32), nullable=False),
    Column("receipt", JSON, nullable=False),
    Column("store_revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "user_id",
        "workspace_id",
        "agent_id",
        "execution_id",
        "purpose",
        "idempotency_key",
        name="uq_persistence_idempotency_scope",
    ),
)
Index(
    "ix_persistence_idempotency_transaction",
    persistence_idempotency.c.transaction_id,
    persistence_idempotency.c.user_id,
    persistence_idempotency.c.execution_id,
)


def create_engine_for_tests(url: str = "sqlite:///:memory:", **kwargs):
    return create_engine(url, future=True, **kwargs)


__all__ = [
    "metadata",
    "persistence_audit",
    "persistence_idempotency",
    "persistence_outbox",
    "persistence_records",
    "create_engine_for_tests",
]
