from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentos.context.models import (
    ContextAssemblyRequest,
    ContextBudget,
    ContextOperationContext,
    ContextSnapshot,
    TaskSnapshot,
    TokenAccounting,
    Provenance,
    ContextCandidate,
    ContextItemKind,
    ContextPriority,
    DataClassification,
    OwnershipScope,
    ContextPolicySnapshot,
    OverflowPolicy,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def test_assembly_request_requires_complete_sensitive_scope():
    with pytest.raises(ValueError):
        ContextAssemblyRequest(
            context=ContextOperationContext(
                user_id="user-1",
                workspace_id="workspace-1",
                agent_id="agent-1",
                execution_id="execution-1",
                correlation_id="correlation-1",
                purpose="",
            ),
            turn=1,
            task=TaskSnapshot(reference="task:1", content="do work"),
            model_requirements_ref="requirements:1",
            budget=ContextBudget(maximum_input_tokens=100),
        )


def test_context_budget_rejects_negative_reservations():
    with pytest.raises(ValueError):
        ContextBudget(maximum_input_tokens=100, reserved_control_tokens=-1)


def test_snapshot_is_reference_focused():
    snapshot = ContextSnapshot(
        execution_id="execution-1",
        turn=1,
        items=(),
        token_accounting=TokenAccounting(),
        context_ref="context:1",
        manifest_ref="manifest:1",
        assembled_at=NOW,
    )

    assert snapshot.manifest_ref == "manifest:1"
    assert "secret" not in repr(snapshot).lower()


def test_provenance_requires_retrieval_timestamp_for_reproducibility():
    with pytest.raises(ValueError):
        Provenance(source_kind="test", source_ref="source:1")


def test_context_candidate_rejects_unknown_contract_enums():
    with pytest.raises(ValueError):
        ContextCandidate(
            candidate_id="candidate:1",
            kind=ContextItemKind.MESSAGE,
            content="message",
            ownership=OwnershipScope("user-1", "workspace-1", "agent-1", "execution-1"),
            provenance=Provenance("TEST", "source:1", retrieved_at=NOW),
            classification="NOT_A_CLASSIFICATION",
            relevance=1.0,
            priority=ContextPriority.NORMAL,
            estimated_tokens=1,
        )


def test_provenance_rejects_unknown_source_kind():
    with pytest.raises(ValueError):
        Provenance("NOT_A_SOURCE", "source:1", retrieved_at=NOW)


def test_context_budget_and_policy_reject_unknown_enum_values():
    with pytest.raises(ValueError):
        ContextBudget(maximum_input_tokens=10, overflow_policy="UNKNOWN")
    with pytest.raises(ValueError):
        ContextPolicySnapshot(
            policy_version="policy:1",
            tokenizer_profile="tokenizer:1",
            source_cutoff_at=NOW,
            classification_ceiling="UNKNOWN",
        )
