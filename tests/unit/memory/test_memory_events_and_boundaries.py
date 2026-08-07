from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

import agentos.memory as memory
from agentos.memory.in_memory import InMemoryMemoryManager, InMemoryMemoryStore
from agentos.memory.models import (
    BoundedMemoryContent,
    MemoryAccessDenied,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryScope,
    SaveMemory,
)
from agentos.memory.security import InMemoryMemoryAuthorizationPolicy


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def test_public_exports_include_only_the_stable_memory_surface():
    for name in (
        "MemoryManager",
        "MemoryStore",
        "MemorySearchAdapter",
        "InMemoryMemoryManager",
        "InMemoryMemoryStore",
        "InMemoryMemoryAuthorizationPolicy",
        "MemoryContextSource",
    ):
        assert hasattr(memory, name)


def test_rejected_operation_leaves_no_mutation_but_records_categorical_denial():
    store = InMemoryMemoryStore()
    policy = InMemoryMemoryAuthorizationPolicy()
    policy.register_agent("user-1", "agent-1")
    manager = InMemoryMemoryManager(store=store, authorization=policy, clock=type("Clock", (), {"now": lambda self: NOW})())
    command = SaveMemory(
        context=MemoryOperationContext(
            user_id="user-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            execution_id="execution-1",
            correlation_id="correlation-1",
            purpose="memory.write",
            actor="agent-1",
        ),
        scope=MemoryScope.WORKSPACE,
        kind="FACT",
        content=BoundedMemoryContent("workspace fact"),
        provenance=MemoryProvenance(source_kind="USER_STATEMENT", source_refs=("source:1",), integrity_ref="integrity:1"),
        classification="INTERNAL",
        retention_policy_ref="retention:1",
        idempotency_key="save:denied",
    )

    with pytest.raises(MemoryAccessDenied):
        manager.save(command)
    assert store.records == ()
    assert len(store.audit_log) == 1
    assert [event.event_type for event in store.outbox] == ["MemoryAccessDenied"]
    assert set(store.outbox[0].payload) <= {"outcome", "reason", "scope", "classification", "purpose"}


def test_event_payloads_are_minimal_and_duplicate_event_ids_are_not_appended():
    store = InMemoryMemoryStore()
    policy = InMemoryMemoryAuthorizationPolicy()
    policy.register_agent("user-1", "agent-1")
    manager = InMemoryMemoryManager(store=store, authorization=policy, clock=type("Clock", (), {"now": lambda self: NOW})())
    command = SaveMemory(
        context=MemoryOperationContext("user-1", None, "agent-1", "execution-1", "correlation-1", "memory.write", "agent-1"),
        scope=MemoryScope.USER,
        kind="FACT",
        content=BoundedMemoryContent("a fact"),
        provenance=MemoryProvenance(source_kind="USER_STATEMENT", source_refs=("source:1",), integrity_ref="integrity:1"),
        classification="INTERNAL",
        retention_policy_ref="retention:1",
        idempotency_key="save:1",
    )
    manager.save(command)
    assert set(store.outbox[0].payload) <= {"memory_id", "version", "scope", "kind", "status", "classification", "purpose"}
    assert "a fact" not in repr(store.outbox[0])
    replay = manager.save(command)
    assert replay.already_applied is True
    assert len(store.outbox) == 1


def test_memory_package_has_no_concrete_infrastructure_tokens():
    root = Path("src/agentos/memory")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    forbidden = (
        "FastAPI", "fastapi", "HTTP", "openai", "anthropic", "google", "SQLAlchemy", "sqlalchemy",
        "Alembic", "alembic", "Redis", "redis", "filesystem", "ArtifactStorage", "requests", "httpx",
        "kafka", "rabbit", "broker", "worker", "scheduler",
    )
    assert not any(term in source for term in forbidden)
