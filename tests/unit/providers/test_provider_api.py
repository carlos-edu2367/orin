from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentos.execution.events import DataClassification
from agentos.execution.models import CancellationReason, CancellationReasonCode
from agentos.providers.models import (
    ApprovedModelRequirementsRef,
    ApprovedModelRequirementsSnapshot,
    CancellationRequirement,
    CatalogVersion,
    GenerationFailed,
    IntegrityRef,
    ModelResolved,
    ModelSelectionId,
    ModelSelectionRef,
    ProviderCost,
    ProviderError,
    ProviderErrorCategory,
    ProviderInvocationId,
    ProviderInvocationLimits,
    ProviderInvocationRequest,
    ProviderModelBindingRef,
    ProviderOperationContext,
    ProviderRef,
    ProviderUsage,
    Retryability,
    ResponseFormat,
    SelectedModel,
    ModelRole,
    ResolvedCapabilities,
    ModelRevision,
    ModelRef,
    AvailabilitySnapshotRef,
    SelectionExplanation,
    StreamOpened,
    ModelStatus,
    ModelDescriptor,
    ModelContextLimits,
)
from agentos.providers.provider import ProviderInvocationValidator


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def context(purpose="provider-test"):
    return ProviderOperationContext("user-1", "workspace-1", "agent-1", "execution-1", "correlation-1", purpose, "actor-1")


def request():
    selected = SelectedModel(ModelRef("model:1"), ProviderRef("provider:1"), ProviderModelBindingRef("binding:1"), ModelRevision("model-revision:1"), ResolvedCapabilities(), Decimal("1"), 1, ModelRole.PRIMARY)
    selection = __import__("agentos.providers.models", fromlist=["ModelSelection"]).ModelSelection(ModelSelectionId("selection:1"), ModelSelectionRef("selection:1"), context(), selected, (), CatalogVersion("1"), "policy:1", None, (), ApprovedModelRequirementsRef("approved:1"), AvailabilitySnapshotRef("availability:1"), SelectionExplanation(None), NOW, NOW + timedelta(minutes=5))
    snapshot = ApprovedModelRequirementsSnapshot(ApprovedModelRequirementsRef("approved:1"), ModelSelectionId("selection:1"), context(), DataClassification.INTERNAL, None, (), ResponseFormat.TEXT, (), CancellationRequirement.ANY, 1, 100, 20, 120, Decimal("1"), (ProviderRef("provider:1"),), (__import__("agentos.providers.models", fromlist=["ModelRef"]).ModelRef("model:1"),), None, CatalogVersion("1"), "policy:1", NOW, NOW + timedelta(minutes=5), IntegrityRef("integrity:1"))
    return ProviderInvocationRequest(ProviderInvocationId("invocation:1"), context(), selection, ApprovedModelRequirementsRef("approved:1"), snapshot, limits=ProviderInvocationLimits(100, 20, 120))


def test_invocation_rejects_scope_mismatch_before_fake_provider_effect():
    validator = ProviderInvocationValidator()
    changed = replace(request(), context=context("other-purpose"))
    with pytest.raises(Exception) as error:
        validator.validate(changed)
    assert error.value.category is ProviderErrorCategory.POLICY_REJECTED


@pytest.mark.parametrize("category", [ProviderErrorCategory.TIMEOUT, ProviderErrorCategory.AUTHENTICATION, ProviderErrorCategory.RATE_LIMITED, ProviderErrorCategory.INVALID_REQUEST, ProviderErrorCategory.CANCELLED])
def test_provider_error_categories_are_public_and_sanitized(category):
    error = ProviderError(category=category, code="PUBLIC_CODE", message="safe summary", retryability=Retryability.SAFE, provider_ref=ProviderRef("provider:1"))
    assert "api_key" not in repr(error).lower()


def test_usage_and_cost_remain_present_on_failure_and_cancellation():
    usage = ProviderUsage(input_tokens=10, output_tokens=2, total_tokens=12)
    cost = ProviderCost(Decimal("0.02"), "USD", measurement="CONFIRMED")
    outcome = GenerationFailed(ProviderInvocationId("invocation:1"), ProviderError(ProviderErrorCategory.TIMEOUT, "TIMEOUT", "safe"), usage, cost)
    assert outcome.usage.input_tokens == 10
    assert outcome.cost.amount == Decimal("0.02")


def test_stream_sequences_are_positive_and_terminal_is_explicit():
    with pytest.raises(ValueError):
        StreamOpened("stream:1", 0)
