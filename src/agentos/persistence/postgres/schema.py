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

# Operational state is explicit and durable. The local worker polls these
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

tool_invocations = Table(
    "tool_invocations", metadata,
    Column("invocation_id", String(255), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(255), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("tool_id", String(255), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("state", String(32), nullable=False),
    Column("fingerprint", String(128), nullable=False),
    Column("outcome", JSON, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)
Index("ix_tool_invocations_scope", tool_invocations.c.user_id, tool_invocations.c.execution_id)


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
    # Retained only for migration compatibility. New credential records never
    # store or interpret a selected model; agent revisions own that selection.
    Column("model", String(255), nullable=True),
    Column("base_url", String(2048), nullable=True),
    Column("secret_ref", String(255), nullable=False),
    # Applied to a key in provider_api_keys after a key-shaped failure
    # (401/403/429/timeout/connection) before it is tried again.
    Column("key_cooldown_seconds", Integer, nullable=False, server_default="60"),
    Column("catalog_refreshed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", name="uq_provider_configurations_user_provider"),
)
Index("ix_provider_configurations_user", provider_configurations.c.user_id)


# Sibling of provider_configurations: N credentials per (user_id, provider),
# ordered by `position` (0 = principal, tried first on every new request).
# A key with status='cooldown' is skipped by
# PostgresProviderApiKeyAdapter.next_available_key until cooldown_until
# passes; see docs/superpowers/specs/
# 2026-08-18-multi-api-key-provider-fallback-design.md.
provider_api_keys = Table(
    "provider_api_keys", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("label", String(255), nullable=True),
    Column("api_key_ciphertext", String(8192), nullable=False),
    Column("secret_ref", String(255), nullable=False),
    Column("position", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("cooldown_until", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", "position", name="uq_provider_api_keys_user_provider_position"),
)
Index("ix_provider_api_keys_user_provider", provider_api_keys.c.user_id, provider_api_keys.c.provider)


provider_model_catalog = Table(
    "provider_model_catalog", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model_id", String(512), nullable=False),
    Column("catalog_base_url", String(2048), nullable=True),
    Column("display_name", String(512), nullable=False),
    Column("context_window", Integer, nullable=True),
    Column("capabilities", JSON, nullable=False),
    Column("input_modalities", JSON, nullable=False),
    Column("output_modalities", JSON, nullable=False),
    Column("input_per_million", String(64), nullable=True),
    Column("output_per_million", String(64), nullable=True),
    Column("route_kind", String(32), nullable=False, server_default="model"),
    Column("is_custom", Boolean, nullable=False, server_default="0"),
    Column("refreshed_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", "model_id", name="uq_provider_model_catalog_scope"),
)
Index("ix_provider_model_catalog_scope", provider_model_catalog.c.user_id, provider_model_catalog.c.provider)


provider_model_favorites = Table(
    "provider_model_favorites", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model_id", String(512), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", "model_id", name="uq_provider_model_favorites_scope"),
)
Index("ix_provider_model_favorites_scope", provider_model_favorites.c.user_id, provider_model_favorites.c.provider)


provider_model_selections = Table(
    "provider_model_selections", metadata,
    Column("id", Integer, primary_key=True),
    Column("selection_ref", String(512), nullable=False, unique=True),
    Column("user_id", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("correlation_id", String(255), nullable=False),
    Column("purpose", String(128), nullable=False),
    Column("model_ref", String(1024), nullable=False),
    Column("model_revision", String(512), nullable=False),
    Column("selection", JSON, nullable=True),
    Column("requirements", JSON, nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
)
Index("ix_provider_model_selections_scope", provider_model_selections.c.user_id, provider_model_selections.c.agent_id)


agent_model_configurations = Table(
    "agent_model_configurations", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("config_version", Integer, nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model_id", String(512), nullable=False),
    Column("selection_ref", String(512), nullable=True),
    Column("model_revision", String(512), nullable=True),
    Column("model_profile_ref", String(1024), nullable=False),
    Column("catalog_refreshed_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "agent_id", "config_version", name="uq_agent_model_configuration_revision"),
)


conversation_prompts = Table(
    "conversation_prompts", metadata,
    Column("id", Integer, primary_key=True),
    Column("prompt_ref", String(255), nullable=False, unique=True),
    Column("user_id", String(255), nullable=False),
    Column("message", String(16000), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_prompts_user", conversation_prompts.c.user_id)

# The product conversation is deliberately separate from the opaque legacy
# prompt reference: it is the public, ordered history the chat UI rehydrates.
conversations = Table(
    "conversations", metadata,
    Column("id", Integer, primary_key=True), Column("conversation_id", String(255), nullable=False, unique=True),
    Column("user_id", String(255), nullable=False), Column("title", String(160), nullable=False),
    Column("provider", String(32), nullable=False), Column("model_id", String(512), nullable=False),
    Column("state", String(32), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False), Column("project_id", String(255), nullable=True),
)
Index("ix_conversations_user_updated", conversations.c.user_id, conversations.c.updated_at)
Index("ix_conversations_project_updated", conversations.c.project_id, conversations.c.updated_at)

projects = Table(
    "projects", metadata,
    Column("project_id", String(255), primary_key=True), Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=False, unique=True), Column("name", String(120), nullable=False),
    Column("description", String(2000), nullable=True), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False), Column("archived_at", DateTime(timezone=True), nullable=True),
)
Index("ix_projects_user_active", projects.c.user_id, projects.c.archived_at, projects.c.updated_at)

workspace_roots = Table(
    "workspace_roots", metadata,
    Column("workspace_id", String(255), primary_key=True), Column("user_id", String(255), nullable=False),
    Column("root_path", String(4096), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

conversation_messages = Table(
    "conversation_messages", metadata,
    Column("id", Integer, primary_key=True), Column("message_id", String(255), nullable=False, unique=True),
    Column("conversation_id", String(255), nullable=False), Column("turn_id", String(255), nullable=True),
    Column("user_id", String(255), nullable=False), Column("role", String(16), nullable=False), Column("content", String(16000), nullable=False),
    Column("sequence", Integer, nullable=False), Column("status", String(32), nullable=False), Column("retryable", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "sequence", name="uq_conversation_message_sequence"),
    CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_message_role"),
)
Index("ix_conversation_messages_history", conversation_messages.c.conversation_id, conversation_messages.c.sequence)

conversation_message_commands = Table(
    "conversation_message_commands", metadata,
    Column("id", Integer, primary_key=True), Column("message_id", String(255), nullable=False, unique=True),
    Column("conversation_id", String(255), nullable=False), Column("user_id", String(255), nullable=False),
    Column("plugin_id", String(64), nullable=False), Column("command_id", String(255), nullable=False),
    Column("arguments", Text(), nullable=False), Column("expanded_body", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_message_commands_conversation", conversation_message_commands.c.conversation_id)

conversation_hook_context = Table(
    "conversation_hook_context", metadata,
    Column("id", Integer, primary_key=True), Column("conversation_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("plugin_id", String(64), nullable=False),
    Column("hook_id", String(255), nullable=False), Column("body", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "hook_id", name="uq_conversation_hook_context"),
)

conversation_turns = Table(
    "conversation_turns", metadata,
    Column("id", Integer, primary_key=True), Column("turn_id", String(255), nullable=False, unique=True),
    Column("conversation_id", String(255), nullable=False), Column("user_id", String(255), nullable=False), Column("execution_id", String(255), nullable=False, unique=True),
    Column("user_message_id", String(255), nullable=False), Column("assistant_message_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False), Column("model_id", String(512), nullable=False), Column("state", String(32), nullable=False),
    Column("idempotency_key", String(255), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("scheduled_by_schedule_id", String(255), nullable=True),
    Column("started_at", DateTime(timezone=True)), Column("finished_at", DateTime(timezone=True)), Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "idempotency_key", name="uq_conversation_turn_idempotency"),
)
Index("ix_conversation_turns_conversation", conversation_turns.c.conversation_id, conversation_turns.c.created_at)
Index("ix_conversation_turns_schedule", conversation_turns.c.scheduled_by_schedule_id, conversation_turns.c.created_at)

scheduled_chat_tasks = Table(
    "scheduled_chat_tasks", metadata,
    Column("task_id", String(255), primary_key=True), Column("schedule_id", String(255), nullable=False, unique=True),
    Column("user_id", String(255), nullable=False), Column("message", String(16000), nullable=False),
    Column("provider", String(32), nullable=False), Column("model_id", String(512), nullable=False),
    Column("project_id", String(255), nullable=True), Column("conversation_id", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["schedule_id"], ["schedules.schedule_id"], name="fk_scheduled_chat_schedule"),
)
Index("ix_scheduled_chat_user", scheduled_chat_tasks.c.user_id, scheduled_chat_tasks.c.updated_at)

conversation_tool_records = Table(
    "conversation_tool_records", metadata,
    Column("id", Integer, primary_key=True), Column("record_id", String(255), nullable=False, unique=True),
    Column("conversation_id", String(255), nullable=False), Column("turn_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("sequence", Integer, nullable=False),
    Column("tool_name", String(64), nullable=False), Column("arguments", String(1000), nullable=False),
    Column("status", String(16), nullable=False), Column("summary", String(512), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "sequence", name="uq_conversation_tool_record_sequence"),
)
Index("ix_conversation_tool_records_conversation", conversation_tool_records.c.conversation_id, conversation_tool_records.c.sequence)

conversation_message_attachments = Table(
    "conversation_message_attachments", metadata,
    Column("id", Integer, primary_key=True), Column("attachment_id", String(255), nullable=False, unique=True),
    Column("message_id", String(255), nullable=False), Column("conversation_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("path", String(1024), nullable=False),
    Column("original_name", String(255), nullable=False), Column("media_type", String(128), nullable=False),
    Column("kind", String(16), nullable=False), Column("bytes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_message_attachments_message", conversation_message_attachments.c.message_id)

vision_model_selections = Table(
    "vision_model_selections", metadata,
    Column("id", Integer, primary_key=True), Column("user_id", String(255), nullable=False, unique=True),
    Column("provider", String(32), nullable=False), Column("model_id", String(512), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

conversation_dispatches = Table(
    "conversation_dispatches", metadata,
    Column("id", Integer, primary_key=True), Column("turn_id", String(255), nullable=False, unique=True), Column("state", String(32), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"), Column("last_error", String(96)), Column("queued_at", DateTime(timezone=True), nullable=False),
    Column("acquired_at", DateTime(timezone=True)), Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_dispatch_state", conversation_dispatches.c.state, conversation_dispatches.c.queued_at)

# The chat transcript is a projection of an execution, not its recovery
# authority.  These records are deliberately narrow: they hold the durable
# decision boundary around an external effect and references to the private
# conversation material needed to rebuild context.  They never feed SSE or
# public event payloads directly.
execution_checkpoints = Table(
    "execution_checkpoints", metadata,
    Column("checkpoint_id", String(255), primary_key=True),
    Column("execution_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False), Column("correlation_id", String(255), nullable=False),
    Column("sequence", Integer, nullable=False), Column("execution_state_version", Integer, nullable=False),
    Column("context_manifest_ref", String(255), nullable=False), Column("next_decision", String(64), nullable=False),
    Column("pending_effect_id", String(255), nullable=True), Column("is_safe", Boolean, nullable=False),
    Column("snapshot", JSON, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("execution_id", "sequence", name="uq_execution_checkpoints_sequence"),
    CheckConstraint("sequence > 0", name="ck_execution_checkpoints_sequence_positive"),
)
Index("ix_execution_checkpoints_latest", execution_checkpoints.c.execution_id, execution_checkpoints.c.sequence)

execution_effects = Table(
    "execution_effects", metadata,
    Column("effect_id", String(255), primary_key=True),
    Column("execution_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("workspace_id", String(255), nullable=True),
    Column("agent_id", String(255), nullable=False), Column("correlation_id", String(255), nullable=False),
    Column("kind", String(32), nullable=False), Column("invocation_ref", String(255), nullable=False),
    Column("request_ref", String(255), nullable=False), Column("result_ref", String(255), nullable=True),
    Column("idempotency_key", String(255), nullable=False), Column("state", String(32), nullable=False),
    Column("retryability", String(32), nullable=False), Column("attempt", Integer, nullable=False),
    Column("result", JSON, nullable=True), Column("error_code", String(128), nullable=True),
    Column("prepared_at", DateTime(timezone=True), nullable=False), Column("started_at", DateTime(timezone=True), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True), Column("reconciliation_ref", String(255), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint("execution_id", "idempotency_key", name="uq_execution_effects_idempotency"),
    CheckConstraint("attempt > 0", name="ck_execution_effects_attempt_positive"),
    CheckConstraint("version > 0", name="ck_execution_effects_version_positive"),
)
Index("ix_execution_effects_recovery", execution_effects.c.execution_id, execution_effects.c.state, execution_effects.c.prepared_at)

# One row per finished turn, recording how well the turn spent its tool calls.
# It answers "did a change to the loop actually make the agent more efficient?"
# with a measurement instead of an impression, so it is written before any
# behavioural change lands and read as the baseline afterwards.
turn_quality_metrics = Table(
    "turn_quality_metrics", metadata,
    Column("turn_id", String(255), primary_key=True),
    Column("conversation_id", String(255), nullable=False), Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False), Column("model_id", String(512), nullable=False),
    Column("tool_calls", Integer, nullable=False), Column("redundant_tool_calls", Integer, nullable=False),
    Column("iterations", Integer, nullable=False),
    Column("input_tokens", Integer, nullable=False), Column("output_tokens", Integer, nullable=False),
    # NULL means the provider never reported cache usage for this turn. Zero
    # would claim we measured and found none, which is a different fact.
    Column("cached_input_tokens", Integer, nullable=True),
    Column("outcome", String(32), nullable=False), Column("error_code", String(64), nullable=True),
    Column("duration_ms", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_turn_quality_metrics_model", turn_quality_metrics.c.user_id, turn_quality_metrics.c.provider, turn_quality_metrics.c.model_id, turn_quality_metrics.c.created_at)

# The agentic trajectory of a turn: which tools were called, with what, and
# what came back. ``conversation_messages`` deliberately cannot hold this --
# its role check exists to protect what the interface renders, and its
# content column is far too small for a tool result -- so the agent's memory
# lives in its own table rather than being welded to the chat projection.
#
# Without this, every tool call and result was discarded at the end of the
# turn, and a follow-up message ("now redo it with the new figures") began
# with no idea what had already been read or written.
conversation_turn_steps = Table(
    "conversation_turn_steps", metadata,
    Column("id", Integer, primary_key=True), Column("step_id", String(255), nullable=False, unique=True),
    Column("conversation_id", String(255), nullable=False), Column("turn_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("agent_id", String(255), nullable=False),
    Column("sequence", Integer, nullable=False), Column("kind", String(24), nullable=False),
    Column("tool_name", String(64), nullable=True), Column("tool_call_id", String(255), nullable=True),
    Column("payload", Text(), nullable=False),
    # The size before any truncation, so a rehydrated result can say honestly
    # how much of it is missing instead of looking complete.
    Column("content_bytes", Integer, nullable=False), Column("truncated", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("turn_id", "agent_id", "sequence", name="uq_conversation_turn_step_sequence"),
)
Index("ix_conversation_turn_steps_turn", conversation_turn_steps.c.conversation_id, conversation_turn_steps.c.turn_id, conversation_turn_steps.c.sequence)

conversation_events = Table(
    "conversation_events", metadata,
    Column("id", Integer, primary_key=True), Column("conversation_id", String(255), nullable=False), Column("user_id", String(255), nullable=False),
    Column("event_type", String(64), nullable=False), Column("message_id", String(255)), Column("payload", JSON, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_events_cursor", conversation_events.c.conversation_id, conversation_events.c.id)

conversation_activity_events = Table(
    "conversation_activity_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", String(255), nullable=False),
    Column("conversation_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("workspace_id", String(255), nullable=True),
    Column("turn_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("parent_agent_id", String(255), nullable=True),
    Column("event_type", String(64), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("summary", String(512), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("visibility", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_id", name="uq_conversation_activity_event_id"),
    UniqueConstraint("user_id", "conversation_id", "turn_id", "sequence", name="uq_conversation_activity_sequence"),
    CheckConstraint("sequence > 0", name="ck_conversation_activity_sequence_positive"),
)
Index("ix_conversation_activity_conversation_cursor", conversation_activity_events.c.conversation_id, conversation_activity_events.c.id)
Index("ix_conversation_activity_user_created", conversation_activity_events.c.user_id, conversation_activity_events.c.created_at)
Index("ix_conversation_activity_turn_sequence", conversation_activity_events.c.turn_id, conversation_activity_events.c.sequence)

agent_memories = Table(
    "agent_memories", metadata,
    Column("memory_id", String(255), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("conversation_id", String(255), nullable=True),
    Column("scope_type", String(16), nullable=False, server_default="user"),
    Column("scope_id", String(255), nullable=False, server_default=""),
    Column("project_id", String(255), nullable=True),
    Column("source_message_id", String(255), nullable=True),
    Column("source_execution_id", String(255), nullable=True),
    Column("fact", String(2000), nullable=False),
    Column("tags", JSON, nullable=False, default=list),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "fact", name="uq_agent_memories_user_fact"),
)
Index("ix_agent_memories_user_updated", agent_memories.c.user_id, agent_memories.c.updated_at)

# Agent Skills are procedural packages.  Keep identity, immutable published
# versions and execution evidence distinct: a current version may move while
# an execution must still explain exactly what it loaded.
skills = Table(
    "skills", metadata,
    Column("id", Integer, primary_key=True),
    Column("skill_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=True),
    Column("workspace_id", String(255), nullable=True),
    Column("scope", String(32), nullable=False),
    Column("source", String(32), nullable=False),
    Column("plugin_id", String(64), nullable=True),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_agent_memories_scope", agent_memories.c.user_id, agent_memories.c.scope_type, agent_memories.c.project_id, agent_memories.c.updated_at)
Index("ix_skills_scope", skills.c.scope, skills.c.user_id, skills.c.workspace_id)
Index("ix_skills_identity", skills.c.skill_id, skills.c.scope, skills.c.user_id, skills.c.workspace_id)

skill_versions = Table(
    "skill_versions", metadata,
    Column("id", Integer, primary_key=True),
    Column("skill_record_id", Integer, nullable=False),
    Column("version", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("instructions", Text, nullable=False),
    Column("content_digest", String(64), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("skill_record_id", "version", name="uq_skill_versions_ref"),
)
Index("ix_skill_versions_skill", skill_versions.c.skill_record_id, skill_versions.c.published_at)

agent_skills = Table(
    "agent_skills", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("skill_version_id", Integer, nullable=False),
    Column("mode", String(32), nullable=False, server_default="pinned"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "agent_id", "skill_version_id", name="uq_agent_skills_ref"),
)
Index("ix_agent_skills_agent", agent_skills.c.user_id, agent_skills.c.agent_id)

execution_skills = Table(
    "execution_skills", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("execution_id", String(255), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("skill_id", String(255), nullable=False),
    Column("version", String(64), nullable=False),
    Column("content_digest", String(64), nullable=False),
    Column("content_snapshot", Text, nullable=False),
    Column("loaded_at", DateTime(timezone=True), nullable=False),
    Column("load_count", Integer, nullable=False, server_default="1"),
    UniqueConstraint("execution_id", "skill_id", "version", name="uq_execution_skills_ref"),
)
Index("ix_execution_skills_execution", execution_skills.c.user_id, execution_skills.c.execution_id)

conversation_agents = Table(
    "conversation_agents", metadata,
    Column("agent_id", String(255), primary_key=True),
    Column("conversation_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("parent_agent_id", String(255), nullable=True),
    Column("name", String(120), nullable=False),
    Column("role", String(512), nullable=False),
    Column("provider", String(32), nullable=True),
    Column("model_id", String(512), nullable=True),
    Column("state", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "name", name="uq_conversation_agents_name"),
)
Index("ix_conversation_agents_conversation", conversation_agents.c.conversation_id)

conversation_agent_usage = Table(
    "conversation_agent_usage", metadata,
    Column("conversation_id", String(255), primary_key=True),
    Column("agent_id", String(255), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model_id", String(512), nullable=False),
    Column("input_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("total_tokens", Integer, nullable=True),
    Column("usage_reported", Boolean, nullable=False, server_default="false"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_agent_usage_conversation", conversation_agent_usage.c.conversation_id)

runtime_heartbeats = Table(
    "runtime_heartbeats", metadata,
    Column("component", String(64), primary_key=True), Column("updated_at", DateTime(timezone=True), nullable=False),
)

mcp_servers = Table(
    "mcp_servers", metadata,
    Column("server_id", String(255), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("slug", String(32), nullable=False),
    Column("display_name", String(255), nullable=False),
    Column("catalog_id", String(64)),
    Column("transport", String(16), nullable=False),
    Column("command", String(512)),
    Column("args", JSON(), nullable=False),
    Column("url", String(2048)),
    Column("secret_names", JSON(), nullable=False),
    Column("secrets_ciphertext", Text()),
    Column("tool_allowlist", JSON()),
    Column("state", String(32), nullable=False),
    Column("state_reason", String(512), nullable=False, server_default=""),
    Column("protocol_version", String(32), nullable=False, server_default=""),
    Column("tools_digest", String(64), nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "slug", name="uq_mcp_servers_slug"),
)
Index("ix_mcp_servers_user", mcp_servers.c.user_id, mcp_servers.c.state)

mcp_server_tools = Table(
    "mcp_server_tools", metadata,
    Column("id", Integer(), primary_key=True),
    Column("server_id", String(255), nullable=False),
    Column("name", String(64), nullable=False),
    Column("description", Text(), nullable=False),
    Column("input_schema", JSON(), nullable=False),
    Column("enabled", Boolean(), nullable=False, server_default="true"),
    Column("discovered_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("server_id", "name", name="uq_mcp_server_tools_ref"),
    ForeignKeyConstraint(["server_id"], ["mcp_servers.server_id"], name="fk_mcp_tools_server", ondelete="CASCADE"),
)
Index("ix_mcp_server_tools_server", mcp_server_tools.c.server_id, mcp_server_tools.c.enabled)

plugins = Table(
    "plugins", metadata,
    Column("plugin_id", String(64), primary_key=True), Column("user_id", String(255), primary_key=True),
    Column("version", String(64), nullable=False), Column("display_name", String(255), nullable=False),
    Column("description", Text, nullable=False, server_default=""), Column("author", String(255), nullable=False, server_default=""),
    Column("homepage", String(512)), Column("source_reference", String(1024), nullable=False),
    Column("install_path", String(1024), nullable=False), Column("package_digest", String(64), nullable=False),
    Column("state", String(32), nullable=False), Column("state_reason", String(512), nullable=False, server_default=""),
    Column("warnings", JSON, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("plugin_id", "user_id", name="uq_plugins_identity"),
)
Index("ix_plugins_user", plugins.c.user_id, plugins.c.state)

plugin_contributions = Table(
    "plugin_contributions", metadata,
    Column("id", Integer, primary_key=True), Column("plugin_id", String(64), nullable=False), Column("user_id", String(255), nullable=False),
    Column("kind", String(32), nullable=False), Column("reference", String(255), nullable=False), Column("display_name", String(255), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="true"), Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("plugin_id", "user_id", "kind", "reference", name="uq_plugin_contributions_ref"),
)
Index("ix_plugin_contributions_plugin", plugin_contributions.c.user_id, plugin_contributions.c.plugin_id)

oauth_tokens = Table(
    "oauth_tokens", metadata,
    Column("user_id", String(255), primary_key=True), Column("provider_id", String(64), primary_key=True),
    Column("access_token_ciphertext", Text(), nullable=False), Column("refresh_token_ciphertext", Text(), nullable=True),
    Column("scope", String(1024), nullable=True), Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
)

plugin_marketplaces = Table(
    "plugin_marketplaces", metadata,
    Column("marketplace_id", String(64), primary_key=True), Column("user_id", String(255), primary_key=True),
    Column("name", String(255), nullable=False), Column("reference", String(1024), nullable=False),
    Column("clone_path", String(1024), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
)


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
    "scheduled_chat_tasks",
    "security_pats",
    "security_sessions",
    "security_revocations",
    "security_rate_limit_hits",
    "event_stream_bindings",
    "multi_agent_events",
    "tool_activity_events",
    "tool_invocations",
    "conversation_activity_events",
    "conversation_agents",
    "conversation_agent_usage",
    "execution_checkpoints",
    "execution_effects",
    "turn_quality_metrics",
    "conversation_turn_steps",
    "conversations",
    "projects",
    "workspace_roots",
    "agent_memories",
    "skills",
    "skill_versions",
    "agent_skills",
    "execution_skills",
    "provider_configurations",
    "provider_api_keys",
    "mcp_servers",
    "mcp_server_tools",
    "plugins",
    "plugin_contributions",
    "plugin_marketplaces",
    "oauth_tokens",
    "create_engine_for_tests",
]
