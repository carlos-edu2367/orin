from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from agentos.context.models import (
    ContextAssemblyRequest,
    ContextBudget,
    ContextCandidate,
    ContextItemKind,
    ContextOperationContext,
    ContextPolicySnapshot,
    ContextPriority,
    ContextTurnUpdate,
    ContentReference,
    DataClassification,
    OwnershipScope,
    Provenance,
    TaskSnapshot,
    TokenAccounting,
    TurnReference,
)
from agentos.context.service import ContextManagerService

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeSource:
    source_kind: str = "test-source"
    candidates: tuple[ContextCandidate, ...] = ()
    queries: list = None

    def __post_init__(self):
        self.queries = []

    def collect(self, query):
        self.queries.append(query)
        return self.candidates


@dataclass
class FakeRecorder:
    manifests: list = None
    finalized: list = None

    def __post_init__(self):
        self.manifests = []
        self.finalized = []

    def record(self, manifest):
        self.manifests.append(manifest)
        return manifest.manifest_id

    def load(self, reference, ownership):
        for manifest in reversed(self.manifests):
            if manifest.manifest_id == reference:
                return manifest
        raise LookupError(reference)

    def finalize(self, execution_id, disposition):
        self.finalized.append((execution_id, disposition))


@dataclass
class FakePolicy:
    def resolve(self, request):
        return ContextPolicySnapshot(
            policy_version="policy:1",
            tokenizer_profile="estimate:v1",
            source_cutoff_at=NOW,
            classification_ceiling=DataClassification.RESTRICTED,
            max_inline_characters=500,
        )


@dataclass
class FakeClock:
    def now(self):
        return NOW


@dataclass
class FakeCancellation:
    cancelled: bool = False

    def is_cancelled(self):
        return self.cancelled


def make_context() -> ContextOperationContext:
    return ContextOperationContext(
        user_id="user-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        purpose="context-test",
    )


def make_request(**changes) -> ContextAssemblyRequest:
    request = ContextAssemblyRequest(
        context=make_context(),
        turn=1,
        task=TaskSnapshot(reference="task:1", content="do work", estimated_tokens=1),
        model_requirements_ref="requirements:1",
        budget=ContextBudget(maximum_input_tokens=100),
    )
    return replace(request, **changes)


def candidate(
    *,
    kind=ContextItemKind.MESSAGE,
    content="message",
    priority=ContextPriority.NORMAL,
    tokens=1,
    user_id="user-1",
    workspace_id="workspace-1",
    agent_id="agent-1",
    execution_id="execution-1",
    candidate_id=None,
):
    return ContextCandidate(
        candidate_id=candidate_id or f"candidate:{kind.value}:{content}",
        kind=kind,
        content=content if isinstance(content, (str, ContentReference)) else str(content),
        ownership=OwnershipScope(user_id, workspace_id, agent_id, execution_id),
        provenance=Provenance(
            source_kind="test-source",
            source_ref="source:1",
            retrieved_at=NOW,
        ),
        classification=DataClassification.INTERNAL,
        relevance=1.0,
        priority=priority,
        estimated_tokens=tokens,
        created_at=NOW,
    )


@pytest.fixture
def context_fixture():
    source = FakeSource()
    recorder = FakeRecorder()
    cancellation = FakeCancellation()
    manager = ContextManagerService(
        sources=(source,),
        recorder=recorder,
        policy=FakePolicy(),
        clock=FakeClock(),
        cancellation=cancellation,
    )
    request = make_request()
    update = ContextTurnUpdate(
        context=request.context,
        expected_turn=1,
        previous_manifest_ref="manifest:missing",
        model_message=TurnReference(reference="message:1", kind=ContextItemKind.MESSAGE),
        usage=TokenAccounting(input_tokens=1, output_tokens=1),
    )
    return type(
        "ContextFixture",
        (),
        {
            "manager": manager,
            "source": source,
            "recorder": recorder,
            "cancellation": cancellation,
            "request": request,
            "update": update,
        },
    )()
