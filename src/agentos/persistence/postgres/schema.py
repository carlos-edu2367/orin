from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
    ForeignKeyConstraint,
)


metadata = MetaData()

persistence_clock = Table(
    "persistence_clock",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("revision", Integer, nullable=False),
    CheckConstraint("id = 1", name="ck_persistence_clock_singleton"),
    CheckConstraint("revision >= 0", name="ck_persistence_clock_revision_nonnegative"),
)

persistence_records = Table(
    "persistence_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("record_ref", String(255), nullable=False),
    Column("record_type", String(96), nullable=False),
    Column("version", Integer, nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("workspace_scope", String(255), nullable=False),
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
    UniqueConstraint(
        "record_ref", "user_id", "workspace_scope", "agent_id", "execution_id",
        "correlation_id", "purpose", "actor", name="uq_persistence_records_ownership",
    ),
    CheckConstraint("version > 0", name="ck_persistence_records_version_positive"),
    CheckConstraint(
        "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_persistence_records_classification",
    ),
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
    Column("workspace_scope", String(255), nullable=False),
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
    ForeignKeyConstraint(
        ["record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
        [
            "persistence_records.record_ref", "persistence_records.user_id",
            "persistence_records.workspace_scope", "persistence_records.agent_id",
            "persistence_records.execution_id", "persistence_records.correlation_id",
            "persistence_records.purpose", "persistence_records.actor",
        ],
        name="fk_persistence_audit_record_scope",
    ),
    CheckConstraint("resulting_version > 0", name="ck_persistence_audit_version_positive"),
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
    Column("workspace_scope", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("classification", String(32), nullable=False),
    Column("event", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("event_id", name="uq_persistence_outbox_event_id"),
    ForeignKeyConstraint(
        ["source_record_ref", "user_id", "workspace_scope", "agent_id", "execution_id", "correlation_id", "purpose", "actor"],
        [
            "persistence_records.record_ref", "persistence_records.user_id",
            "persistence_records.workspace_scope", "persistence_records.agent_id",
            "persistence_records.execution_id", "persistence_records.correlation_id",
            "persistence_records.purpose", "persistence_records.actor",
        ],
        name="fk_persistence_outbox_source_scope",
    ),
    CheckConstraint("expected_source_version > 0", name="ck_persistence_outbox_version_positive"),
    CheckConstraint(
        "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_persistence_outbox_classification",
    ),
)
Index("ix_persistence_outbox_pending", persistence_outbox.c.published_at, persistence_outbox.c.created_at)
Index("ix_persistence_outbox_scope", persistence_outbox.c.user_id, persistence_outbox.c.workspace_id, persistence_outbox.c.execution_id)

persistence_idempotency = Table(
    "persistence_idempotency",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("workspace_scope", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("fingerprint", String(128), nullable=False),
    Column("transaction_id", String(128), nullable=False),
    Column("commit_state", String(32), nullable=False),
    Column("receipt", JSON, nullable=False),
    Column("records", JSON, nullable=False),
    Column("store_revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "user_id",
        "workspace_scope",
        "agent_id",
        "execution_id",
        "correlation_id",
        "purpose",
        "actor",
        "idempotency_key",
        name="uq_persistence_idempotency_scope",
    ),
    CheckConstraint("store_revision >= 0", name="ck_persistence_idempotency_revision_nonnegative"),
    CheckConstraint(
        "workspace_scope = COALESCE(workspace_id, '')",
        name="ck_persistence_idempotency_workspace_scope",
    ),
    CheckConstraint(
        "commit_state IN ('COMMITTED', 'NOT_COMMITTED', 'UNKNOWN')",
        name="ck_persistence_idempotency_commit_state",
    ),
)
Index(
    "ix_persistence_idempotency_transaction",
    persistence_idempotency.c.transaction_id,
    persistence_idempotency.c.user_id,
    persistence_idempotency.c.execution_id,
)

# Operational state is explicit and durable. Redis/ARQ only materializes these
# records; it never decides their existence, version, or terminal state.
dispatches = Table(
    "worker_dispatches", metadata,
    Column("id", Integer, primary_key=True),
    Column("dispatch_id", String(255), nullable=False, unique=True),
    Column("execution_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("workspace_id", String(255)),
    Column("agent_id", String(255), nullable=False), Column("pool", String(32), nullable=False),
    Column("work_kind", String(64), nullable=False), Column("state", String(32), nullable=False),
    Column("version", Integer, nullable=False), Column("idempotency_key", String(256), nullable=False),
    Column("payload_ref", String(255), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "workspace_id", "agent_id", "execution_id", "idempotency_key", name="uq_worker_dispatch_idempotency"),
    CheckConstraint("version > 0", name="ck_worker_dispatch_version"),
)
Index("ix_worker_dispatches_execution_state", dispatches.c.execution_id, dispatches.c.state)

dispatch_attempts = Table(
    "worker_dispatch_attempts", metadata,
    Column("id", Integer, primary_key=True), Column("dispatch_attempt_id", String(255), nullable=False, unique=True),
    Column("dispatch_id", String(255), nullable=False), Column("attempt_number", Integer, nullable=False),
    Column("state", String(32), nullable=False), Column("version", Integer, nullable=False),
    Column("lease_id", String(255)), Column("worker_id", String(255)), Column("fencing_token", Integer),
    Column("lease_expires_at", DateTime(timezone=True)), Column("reason_code", String(96)),
    Column("not_before", DateTime(timezone=True), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["dispatch_id"], ["worker_dispatches.dispatch_id"], name="fk_worker_attempt_dispatch"),
    UniqueConstraint("dispatch_id", "attempt_number", name="uq_worker_attempt_number"),
    CheckConstraint("version > 0 AND attempt_number > 0", name="ck_worker_attempt_version"),
)
Index("ix_worker_attempts_recovery", dispatch_attempts.c.state, dispatch_attempts.c.lease_expires_at, dispatch_attempts.c.expires_at)

schedules = Table(
    "schedules", metadata,
    Column("id", Integer, primary_key=True), Column("schedule_id", String(255), nullable=False, unique=True),
    Column("user_id", String(255), nullable=False), Column("workspace_id", String(255)), Column("agent_id", String(255), nullable=False),
    Column("schedule_type", String(32), nullable=False), Column("target", JSON, nullable=False), Column("rule", JSON, nullable=False),
    Column("timezone", String(128), nullable=False), Column("policies", JSON, nullable=False), Column("state", String(32), nullable=False), Column("version", Integer, nullable=False),
    Column("next_fire_at", DateTime(timezone=True)), Column("starts_at", DateTime(timezone=True), nullable=False), Column("ends_at", DateTime(timezone=True)),
    Column("idempotency_key", String(256), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("version > 0", name="ck_schedule_version"),
)
Index("ix_schedules_due", schedules.c.state, schedules.c.next_fire_at)

schedule_occurrences = Table(
    "schedule_occurrences", metadata,
    Column("id", Integer, primary_key=True), Column("occurrence_id", String(512), nullable=False, unique=True), Column("schedule_id", String(255), nullable=False),
    Column("schedule_version", Integer, nullable=False), Column("logical_scheduled_at", DateTime(timezone=True), nullable=False),
    Column("state", String(32), nullable=False), Column("version", Integer, nullable=False), Column("state_fencing_token", Integer, nullable=False),
    Column("claim_id", String(512)), Column("claim_owner", String(255)), Column("claim_expires_at", DateTime(timezone=True)),
    Column("execution_id", String(255)), Column("dispatch_id", String(255)), Column("dispatch_attempt_count", Integer, nullable=False, server_default="0"),
    Column("reason_code", String(96)), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["schedule_id"], ["schedules.schedule_id"], name="fk_occurrence_schedule"),
    UniqueConstraint("schedule_id", "schedule_version", "logical_scheduled_at", name="uq_schedule_occurrence_logical"),
    CheckConstraint("version > 0 AND state_fencing_token >= 0", name="ck_schedule_occurrence_version"),
)
Index("ix_schedule_occurrence_recovery", schedule_occurrences.c.state, schedule_occurrences.c.claim_expires_at)


security_pats = Table(
    "security_pats", metadata,
    Column("id", Integer, primary_key=True),
    Column("credential_ref", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("token_digest", String(64), nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("revoked", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("credential_ref", name="uq_security_pats_credential_ref"),
    UniqueConstraint("token_digest", name="uq_security_pats_token_digest"),
)
Index("ix_security_pats_user", security_pats.c.user_id)

security_sessions = Table(
    "security_sessions", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("credential_ref", String(255), nullable=False),
    Column("csrf_digest", String(64), nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("revoked", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("session_id", name="uq_security_sessions_session_id"),
)
Index("ix_security_sessions_credential_ref", security_sessions.c.credential_ref)

security_revocations = Table(
    "security_revocations", metadata,
    Column("id", Integer, primary_key=True),
    Column("credential_ref", String(255), nullable=False),
    Column("epoch", Integer, nullable=False),
    UniqueConstraint("credential_ref", name="uq_security_revocations_credential_ref"),
    CheckConstraint("epoch >= 0", name="ck_security_revocations_epoch_nonnegative"),
)

security_rate_limit_hits = Table(
    "security_rate_limit_hits", metadata,
    Column("id", Integer, primary_key=True),
    Column("credential_ref", String(255), nullable=False),
    Column("action", String(128), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)
Index("ix_security_rate_limit_window", security_rate_limit_hits.c.credential_ref, security_rate_limit_hits.c.action, security_rate_limit_hits.c.occurred_at)


event_stream_bindings = Table(
    "event_stream_bindings", metadata,
    Column("id", Integer, primary_key=True),
    Column("stream_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("credential_ref", String(255), nullable=False),
    Column("execution_ids", JSON, nullable=False),
    Column("digest", String(64), nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("stream_id", name="uq_event_stream_bindings_stream_id"),
)
Index("ix_event_stream_bindings_user", event_stream_bindings.c.user_id)


# Frontend Fase B: durable bridges for facts that do not flow through
# persistence_outbox today. Neither table has a FK to persistence_records:
# a delegation fact or a tool invocation is not a versioned "record" of an
# execution the way persistence_outbox's source_record_ref is (see
# docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md, Fase B.1).
multi_agent_events = Table(
    "multi_agent_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", String(255), nullable=False),
    Column("event_type", String(96), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=True),
    Column("execution_id", String(255), nullable=True),
    Column("correlation_id", String(255), nullable=False),
    Column("causation_id", String(255), nullable=True),
    Column("sequence", Integer, nullable=True),
    Column("classification", String(32), nullable=False),
    Column("event", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_id", name="uq_multi_agent_events_event_id"),
)
Index("ix_multi_agent_events_scope", multi_agent_events.c.user_id, multi_agent_events.c.execution_id)

tool_activity_events = Table(
    "tool_activity_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", String(255), nullable=False),
    Column("event_type", String(96), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("invocation_id", String(255), nullable=False),
    Column("event", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_id", name="uq_tool_activity_events_event_id"),
)
Index("ix_tool_activity_events_scope", tool_activity_events.c.user_id, tool_activity_events.c.execution_id)


# Frontend Fase D: user-configured LLM provider credentials. There is no
# execution/agent scope for a provider configuration (it is set once per
# user, independent of any execution), so this is a dedicated table rather
# than a reuse of persistence_records's execution-shaped scope columns.
provider_configurations = Table(
    "provider_configurations", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("model", String(255), nullable=False),
    Column("api_key", String(4096), nullable=False),
    Column("secret_ref", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", name="uq_provider_configurations_user_provider"),
)
Index("ix_provider_configurations_user", provider_configurations.c.user_id)


def create_engine_for_tests(url: str = "sqlite:///:memory:", **kwargs):
    return create_engine(url, future=True, **kwargs)


__all__ = [
    "metadata",
    "persistence_audit",
    "persistence_idempotency",
    "persistence_outbox",
    "persistence_records",
    "persistence_clock",
    "dispatches",
    "dispatch_attempts",
    "schedules",
    "schedule_occurrences",
    "security_pats",
    "security_sessions",
    "security_revocations",
    "security_rate_limit_hits",
    "event_stream_bindings",
    "multi_agent_events",
    "tool_activity_events",
    "provider_configurations",
    "create_engine_for_tests",
]
