from datetime import datetime, timedelta, timezone

import pytest

from agentos.context import (
    AuthorizedSourceReference,
    AuthorizeContextShare,
    ContextShareBudget,
    CreateSharedContextReference,
    CreateStructuredHandoff,
    HandoffRef,
    ResolveSharedContext,
    RevokeContextShare,
    SharedContextKind,
)
from agentos.context.sharing import TaskSnapshot
from agentos.context.models import AuthorizedContextQuery, ContextItemKind, ContextOperationContext
from agentos.events import DataClassification
from agentos.memory import InMemoryMemoryManager, InMemoryMemorySharingService, InMemoryMemoryStore, MemoryContextSource
from agentos.memory.models import (
    BoundedMemoryContent,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryReference,
    MemoryScope,
    SaveMemory,
)
from agentos.memory.security import InMemoryMemoryAuthorizationPolicy


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = NOW

    def now(self):
        return self.value


def context(agent_id="agent:source", execution_id="execution:source", purpose="memory.write", workspace_id="workspace:1"):
    return MemoryOperationContext(
        user_id="user:1",
        workspace_id=workspace_id,
        agent_id=agent_id,
        execution_id=execution_id,
        correlation_id="correlation:share",
        purpose=purpose,
        actor=agent_id,
    )


def setup():
    policy = InMemoryMemoryAuthorizationPolicy()
    policy.register_agent("user:1", "agent:source")
    policy.register_agent("user:1", "agent:target")
    policy.register_workspace_access("user:1", "workspace:1", "agent:source")
    policy.register_workspace_access("user:1", "workspace:1", "agent:target")
    store = InMemoryMemoryStore()
    manager = InMemoryMemoryManager(store=store, authorization=policy, clock=Clock())
    receipt = manager.save(
        SaveMemory(
            context=context(),
            scope=MemoryScope.PRIVATE,
            kind="FACT",
            content=BoundedMemoryContent("bounded shared fact"),
            provenance=MemoryProvenance(
                source_kind="USER_STATEMENT",
                source_refs=("source:share",),
                integrity_ref="integrity:share",
            ),
            classification=DataClassification.INTERNAL,
            retention_policy_ref="retention:1",
            idempotency_key="save:share",
            expires_at=NOW + timedelta(days=1),
        )
    )
    service = InMemoryMemorySharingService(manager=manager, clock=Clock())
    return manager, service, receipt


def grant(service):
    return service.authorize(
        AuthorizeContextShare(
            actor="agent:source",
            execution_id="execution:source",
            user_id="user:1",
            workspace_id="workspace:1",
            source_agent_id="agent:source",
            target_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            purpose="memory.share",
            requested_kinds=(SharedContextKind.MEMORY,),
            filters=(),
            budget=ContextShareBudget(4, 0, 0, 32),
            classification_ceiling=DataClassification.INTERNAL,
            consumption_policy="MULTI_USE_UNTIL_TERMINAL",
            expires_at=NOW + timedelta(minutes=10),
            correlation_id="correlation:share",
            idempotency_key="authorize:share",
            authorization_ref="auth:share",
        )
    )


def test_memory_reference_resolves_through_canonical_grant_and_handoff():
    manager, service, receipt = setup()
    shared_grant = grant(service)
    created = service.create_reference(
        CreateSharedContextReference(
            actor="agent:source",
            execution_id="execution:source",
            user_id="user:1",
            workspace_id="workspace:1",
            source_agent_id="agent:source",
            target_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            grant_id=shared_grant.grant_id,
            source_ref=AuthorizedSourceReference(
                source_kind=SharedContextKind.MEMORY,
                source_ref=receipt.memory_id,
                source_version=receipt.version,
                user_id="user:1",
                workspace_id="workspace:1",
                owner_agent_id="agent:source",
                authorization_ref="owner:agent:source",
                permitted_purposes=("memory.share",),
                classification=DataClassification.INTERNAL,
                expires_at=NOW + timedelta(minutes=5),
                integrity_ref="integrity:share",
            ),
            source_kind=SharedContextKind.MEMORY,
            expected_source_version=receipt.version,
            purpose="memory.share",
            correlation_id="correlation:share",
            idempotency_key="reference:share",
        )
    )
    handoff = service.create_handoff(
        CreateStructuredHandoff(
            actor="agent:source",
            execution_id="execution:source",
            user_id="user:1",
            workspace_id="workspace:1",
            source_agent_id="agent:source",
            target_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            grant_id=created.grant_id,
            objective=TaskSnapshot("task:share", "Use the shared fact", 1, NOW, "integrity:task"),
            success_criteria=(),
            constraints=(),
            expected_output=None,
            context_refs=(created,),
            minimal_snapshot_ref=None,
            delegated_grant_refs=(),
            budget=ContextShareBudget(4, 0, 0, 32),
            purpose="TASK_DELEGATION",
            correlation_id="correlation:share",
            idempotency_key="handoff:share",
        )
    )
    resolved = service.resolve(
        ResolveSharedContext(
            actor="agent:target",
            execution_id="execution:target",
            user_id="user:1",
            workspace_id="workspace:1",
            source_agent_id="agent:source",
            target_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            grant_id=created.grant_id,
            handoff_ref=handoff,
            requested_ref_ids=(created.shared_ref_id,),
            purpose="memory.share",
            remaining_budget=ContextShareBudget(4, 0, 0, 32),
            expected_resolution_count=0,
            correlation_id="correlation:share",
            idempotency_key="resolve:share",
        )
    )

    assert resolved.authorized_candidates == (created,)
    assert resolved.resolution_count == 1
    source = MemoryContextSource(manager, shared_service=service)
    context_query = AuthorizedContextQuery(
        context=ContextOperationContext(
            "user:1", "workspace:1", "agent:target", "execution:target", "correlation:context", "memory.share"
        ),
        cutoff_at=NOW,
        classification_ceiling=DataClassification.INTERNAL,
        allowed_kinds=(ContextItemKind.MEMORY_REFERENCE,),
        purpose="memory.share",
    )
    assert source.collect_shared(context_query, resolved)
    service.revoke(replace_command(shared_grant, "revoke:after-resolve"))
    assert source.collect_shared(context_query, resolved) == ()


def test_revoked_share_fails_closed_and_retry_is_idempotent():
    _, service, _ = setup()
    current = grant(service)
    revoked = service.revoke(
        RevokeContextShare(
            actor="agent:source",
            execution_id="execution:source",
            user_id="user:1",
            workspace_id="workspace:1",
            source_agent_id="agent:source",
            target_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            grant_id=current.grant_id,
            reason="policy",
            purpose="memory.share",
            correlation_id="correlation:share",
            idempotency_key="revoke:share",
        )
    )

    assert revoked.status.value == "REVOKED"
    assert service.revoke(replace_command(revoked, "revoke:share")).status.value == "REVOKED"


def test_user_memory_can_be_exposed_to_workspace_only_by_explicit_grant():
    policy = InMemoryMemoryAuthorizationPolicy()
    policy.register_agent("user:1", "agent:source")
    policy.register_agent("user:1", "agent:target")
    policy.register_workspace_access("user:1", "workspace:1", "agent:target")
    manager = InMemoryMemoryManager(store=InMemoryMemoryStore(), authorization=policy, clock=Clock())
    receipt = manager.save(
        SaveMemory(
            context=context(workspace_id=None),
            scope=MemoryScope.USER,
            kind="PREFERENCE",
            content=BoundedMemoryContent("bounded user preference"),
            provenance=MemoryProvenance(source_kind="USER_STATEMENT", source_refs=("source:user",), integrity_ref="integrity:user"),
            classification=DataClassification.INTERNAL,
            retention_policy_ref="retention:1",
            idempotency_key="save:user",
        )
    )
    service = InMemoryMemorySharingService(manager=manager, clock=Clock())
    shared_grant = grant(service)
    shared = service.create_reference(
        CreateSharedContextReference(
            actor="agent:source",
            execution_id="execution:source",
            user_id="user:1",
            workspace_id="workspace:1",
            source_agent_id="agent:source",
            target_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            grant_id=shared_grant.grant_id,
            source_ref=AuthorizedSourceReference(
                source_kind=SharedContextKind.MEMORY,
                source_ref=receipt.memory_id,
                source_version=receipt.version,
                user_id="user:1",
                workspace_id=None,
                owner_agent_id=None,
                authorization_ref="owner:user:1",
                permitted_purposes=("memory.share",),
                classification=DataClassification.INTERNAL,
                expires_at=NOW + timedelta(minutes=5),
                integrity_ref="integrity:user",
            ),
            source_kind=SharedContextKind.MEMORY,
            expected_source_version=receipt.version,
            purpose="memory.share",
            correlation_id="correlation:share:user",
            idempotency_key="reference:user",
        )
    )

    assert shared.source_workspace_id is None
    assert shared.target_agent_id == "agent:target"


def replace_command(receipt, key):
    return RevokeContextShare(
        actor="agent:source",
        execution_id="execution:source",
        user_id="user:1",
        workspace_id="workspace:1",
        source_agent_id="agent:source",
        target_agent_id="agent:target",
        source_execution_id="execution:source",
        target_execution_id="execution:target",
        grant_id=receipt.grant_id,
        reason="policy",
        purpose="memory.share",
        correlation_id="correlation:share",
        idempotency_key=key,
    )
