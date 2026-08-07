from datetime import datetime, timezone

import pytest

from agentos.events import DataClassification
from agentos.memory.models import (
    BoundedMemoryContent,
    MemoryKind,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryRecord,
    MemoryReference,
    MemoryScope,
    MemoryStatus,
    SaveMemory,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def valid_provenance(**overrides):
    values = {
        "source_kind": "USER_STATEMENT",
        "source_refs": ("source:1",),
        "authored_by": "user-1",
        "observed_at": NOW,
        "confidence": 0.9,
        "transformation_chain": (),
        "integrity_ref": "integrity:1",
    }
    values.update(overrides)
    return MemoryProvenance(**values)


def valid_context(**overrides):
    values = {
        "user_id": "user-1",
        "workspace_id": None,
        "agent_id": "agent-1",
        "execution_id": "execution-1",
        "correlation_id": "correlation-1",
        "purpose": "memory-test",
        "actor": "agent-1",
    }
    values.update(overrides)
    return MemoryOperationContext(**values)


def test_save_requires_complete_context_and_provenance():
    with pytest.raises(ValueError):
        SaveMemory(
            context=valid_context(purpose=""),
            scope=MemoryScope.USER,
            kind=MemoryKind.FACT,
            content=BoundedMemoryContent("fact"),
            provenance=valid_provenance(integrity_ref=None),
            classification=DataClassification.INTERNAL,
            retention_policy_ref="retention:1",
            idempotency_key="idem:1",
        )


def test_scope_invariants_are_rejected():
    with pytest.raises(ValueError):
        MemoryRecord(
            memory_id="memory:1",
            user_id="user-1",
            workspace_id=None,
            owner_agent_id=None,
            scope=MemoryScope.PRIVATE,
            base_scope=MemoryScope.PRIVATE,
            kind=MemoryKind.FACT,
            content=BoundedMemoryContent("x"),
            provenance=valid_provenance(),
            classification=DataClassification.INTERNAL,
            retention_policy_ref="retention:1",
            status=MemoryStatus.ACTIVE,
            version=1,
            created_by="agent-1",
            created_execution_id="execution-1",
            correlation_id="correlation-1",
            created_at=NOW,
            valid_from=NOW,
        )


def test_public_representations_are_opaque_for_content_and_references():
    content = BoundedMemoryContent("secret-value")
    reference = MemoryReference(
        memory_id="memory:1",
        version=1,
        user_id="user-1",
        workspace_id=None,
        permitted_agent_id="agent-1",
        authorization_ref="grant:1",
        purpose="read",
        expires_at=None,
        integrity_ref="hash:1",
    )

    assert "secret-value" not in repr(content)
    assert "physical/path" not in repr(reference)
