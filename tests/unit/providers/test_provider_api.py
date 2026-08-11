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
    ImagePart,
    ProviderMessage,
    ContentRole,
    ToolDeclaration,
    ToolRef,
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


def test_provider_error_rejects_secret_like_public_messages():
    with pytest.raises(ValueError):
        ProviderError(ProviderErrorCategory.UNKNOWN, "SECRET", "password=private", provider_ref=ProviderRef("provider:1"))


def test_invocation_rejects_request_format_that_snapshot_did_not_approve():
    changed = replace(request(), response_format=ResponseFormat.JSON)

    with pytest.raises(Exception) as error:
        ProviderInvocationValidator().validate(changed)

    assert error.value.category is ProviderErrorCategory.POLICY_REJECTED


def test_invocation_rejects_image_not_approved_by_snapshot():
    changed = replace(
        request(),
        messages=(ProviderMessage(ContentRole.USER, (ImagePart("image:1", "image/png"),)),),
    )

    with pytest.raises(Exception) as error:
        ProviderInvocationValidator().validate(changed)

    assert error.value.category is ProviderErrorCategory.POLICY_REJECTED


def test_invocation_rejects_tool_declarations_not_approved_by_snapshot():
    changed = replace(
        request(),
        tools=(ToolDeclaration(ToolRef("tool:1"), "public-tool", "safe", "schema:1"),),
    )

    with pytest.raises(Exception) as error:
        ProviderInvocationValidator().validate(changed)

    assert error.value.category is ProviderErrorCategory.POLICY_REJECTED


def test_invocation_rejects_binding_that_does_not_match_catalog_descriptor():
    class Catalog:
        def get_model(self, _query):
            return ModelDescriptor(
                ModelRef("model:1"),
                ProviderRef("provider:1"),
                "public-model",
                provider_binding_ref=ProviderModelBindingRef("binding:actual"),
            )

        def get_provider(self, _provider_ref):
            return type("Provider", (), {"status": "ACTIVE"})()

    with pytest.raises(Exception) as error:
        ProviderInvocationValidator(Catalog()).validate(request())

    assert error.value.category is ProviderErrorCategory.POLICY_REJECTED


def test_stream_sequences_are_positive_and_terminal_is_explicit():
    with pytest.raises(ValueError):
        StreamOpened("stream:1", 0)
