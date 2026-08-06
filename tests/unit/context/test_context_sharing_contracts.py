from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentos.context import (
    ContextShareBudget,
    ContextShareGrant,
    DataClassification,
    DelegatedGrantRef,
    HandoffRef,
    OutputContractRef,
    SharedContextReference,
    StructuredHandoff,
)
from agentos.context.sharing import TaskSnapshot


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


def _budget() -> ContextShareBudget:
    return ContextShareBudget(
        maximum_references=4,
        maximum_snapshot_items=2,
        maximum_summary_units=16,
        maximum_resolved_content_units=32,
    )


def _grant() -> ContextShareGrant:
    return ContextShareGrant(
        grant_id="grant:1",
        user_id="user:1",
        workspace_id="workspace:1",
        source_agent_id="agent:source",
        target_agent_id="agent:target",
        source_execution_id="execution:source",
        target_execution_id="execution:target",
        purpose="task.delegation",
        classification_ceiling=DataClassification.CONFIDENTIAL,
        budget=_budget(),
        redelegation=False,
        authorization_ref="auth:1",
        correlation_id="corr:1",
        issued_at=NOW,
        expires_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
    )


def _reference() -> SharedContextReference:
    return SharedContextReference(
        shared_ref_id="shared:1",
        grant_id="grant:1",
        source_kind="RESULT",
        source_ref="result:1",
        source_version=1,
        source_user_id="user:1",
        source_workspace_id="workspace:1",
        source_agent_id="agent:source",
        target_agent_id="agent:target",
        target_execution_id="execution:target",
        purpose="task.delegation",
        classification=DataClassification.INTERNAL,
        integrity_ref="integrity:1",
        created_at=NOW,
        expires_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
    )


def test_structured_handoff_is_immutable_and_uses_bounded_opaque_references():
    handoff = StructuredHandoff(
        handoff_id="handoff:1",
        grant_id="grant:1",
        user_id="user:1",
        workspace_id="workspace:1",
        from_agent_id="agent:source",
        to_agent_id="agent:target",
        source_execution_id="execution:source",
        target_execution_id="execution:target",
        objective=TaskSnapshot(task_id="task:1", objective="Review the bounded result", source_version=1, captured_at=NOW, integrity_ref="integrity:task"),
        success_criteria=(),
        constraints=(),
        expected_output=OutputContractRef(
            output_contract_id="output:1",
            version=1,
            expected_kind="REPORT",
            schema_ref="schema:1",
            authorization_ref="auth:1",
            integrity_ref="integrity:output",
        ),
        context_refs=(_reference(),),
        minimal_snapshot_ref=None,
        delegated_grant_refs=(
            DelegatedGrantRef(
                delegated_grant_id="delegated:1",
                parent_grant_id="grant:1",
                from_agent_id="agent:source",
                to_agent_id="agent:target",
                target_execution_id="execution:target",
                allowed_kinds=("RESULT",),
                purpose="task.delegation",
                redelegation=False,
                expires_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
                authorization_ref="auth:delegated",
                integrity_ref="integrity:delegated",
            ),
        ),
        budget=_budget(),
        purpose="TASK_DELEGATION",
        classification=DataClassification.INTERNAL,
        correlation_id="corr:1",
        version=1,
        integrity_ref="integrity:handoff",
        created_at=NOW,
        expires_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
    )

    assert handoff.context_refs == (_reference(),)
    assert handoff.expires_at == handoff.context_refs[0].expires_at
    with pytest.raises(AttributeError):
        handoff.version = 2


def test_handoff_ref_requires_matching_scope_and_non_expired_integrity():
    ref = HandoffRef(
        handoff_id="handoff:1",
        grant_id="grant:1",
        from_agent_id="agent:source",
        to_agent_id="agent:target",
        source_execution_id="execution:source",
        target_execution_id="execution:target",
        purpose="TASK_DELEGATION",
        classification=DataClassification.INTERNAL,
        version=1,
        expires_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
        integrity_ref="integrity:handoff",
    )
    assert ref.handoff_id == "handoff:1"

    with pytest.raises(ValueError, match="expires_at"):
        HandoffRef(
            handoff_id="handoff:1",
            grant_id="grant:1",
            from_agent_id="agent:source",
            to_agent_id="agent:target",
            source_execution_id="execution:source",
            target_execution_id="execution:target",
            purpose="TASK_DELEGATION",
            classification=DataClassification.INTERNAL,
            version=1,
            expires_at=None,
            integrity_ref="integrity:handoff",
        )


def test_grant_rejects_cross_scope_reference_and_classification_over_ceiling():
    grant = _grant()
    with pytest.raises(ValueError, match="classification"):
        replace(_reference(), classification=DataClassification.RESTRICTED).validate_against(grant)
    assert grant.redelegation is False
