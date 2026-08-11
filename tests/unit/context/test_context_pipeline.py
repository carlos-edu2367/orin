from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentos.context.models import (
    ContextError,
    ContextErrorCategory,
    ContextItemKind,
    ContextPriority,
    ContextBudget,
    ContextCandidate,
    ContentReference,
    DataClassification,
    OverflowPolicy,
    OwnershipScope,
    Provenance,
    SourceKind,
)


def candidate(
    *,
    kind=ContextItemKind.MESSAGE,
    content="message",
    priority=ContextPriority.NORMAL,
    tokens=1,
    workspace_id="workspace-1",
):
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    return ContextCandidate(
        candidate_id=f"candidate:{kind.value}:{content}",
        kind=kind,
        content=content if isinstance(content, (str, ContentReference)) else str(content),
        ownership=OwnershipScope("user-1", workspace_id, "agent-1", "execution-1"),
        provenance=Provenance(SourceKind.TEST, "source:1", retrieved_at=now),
        classification=DataClassification.INTERNAL,
        relevance=1.0,
        priority=priority,
        estimated_tokens=tokens,
        created_at=now,
    )


def test_assemble_sends_complete_scope_to_every_source(context_fixture):
    context_fixture.manager.assemble(context_fixture.request)
    query = context_fixture.source.queries[0]
    assert query.context.user_id == "user-1"
    assert query.context.workspace_id == "workspace-1"
    assert query.context.agent_id == "agent-1"
    assert query.context.execution_id == "execution-1"
    assert query.context.correlation_id == "correlation-1"
    assert query.context.purpose == "context-test"


def test_optional_secret_candidate_is_excluded_without_leaking_value(context_fixture):
    context_fixture.source.candidates = (
        candidate(kind=ContextItemKind.MESSAGE, content="api_key=super-secret-value"),
    )
    snapshot = context_fixture.manager.assemble(context_fixture.request)
    assert all("super-secret-value" not in repr(item) for item in snapshot.items)
    manifest = context_fixture.recorder.manifests[-1]
    assert any(record.reason == "SANITIZATION_FAILED" for record in manifest.excluded)


def test_cross_workspace_candidate_is_rejected(context_fixture):
    context_fixture.source.candidates = (candidate(workspace_id="other-workspace"),)
    with pytest.raises(ContextError) as error:
        context_fixture.manager.assemble(context_fixture.request)
    assert error.value.category is ContextErrorCategory.OWNERSHIP


def test_required_items_are_preserved_before_optional_items(context_fixture):
    context_fixture.request = replace(
        context_fixture.request,
        budget=ContextBudget(maximum_input_tokens=5, overflow_policy=OverflowPolicy.EXCLUDE_OPTIONAL),
    )
    context_fixture.source.candidates = (
        candidate(kind=ContextItemKind.MESSAGE, priority=ContextPriority.LOW, tokens=4),
        candidate(kind=ContextItemKind.CONTROL_STATE, priority=ContextPriority.REQUIRED, tokens=1),
    )
    snapshot = context_fixture.manager.assemble(context_fixture.request)
    assert [item.kind for item in snapshot.items] == [ContextItemKind.CONTROL_STATE, ContextItemKind.TASK]


def test_required_item_that_cannot_fit_raises_budget_error(context_fixture):
    context_fixture.request = replace(
        context_fixture.request,
        budget=ContextBudget(maximum_input_tokens=2),
    )
    context_fixture.source.candidates = (candidate(priority=ContextPriority.REQUIRED, tokens=3),)
    with pytest.raises(ContextError) as error:
        context_fixture.manager.assemble(context_fixture.request)
    assert error.value.category is ContextErrorCategory.BUDGET


def test_manifest_has_only_references_and_categorical_reasons(context_fixture):
    snapshot = context_fixture.manager.assemble(context_fixture.request)
    manifest = context_fixture.recorder.manifests[-1]
    assert snapshot.manifest_ref == manifest.manifest_id
    assert all(record.content is None for record in manifest.included)
    assert all("secret" not in repr(record).lower() for record in manifest.excluded)
