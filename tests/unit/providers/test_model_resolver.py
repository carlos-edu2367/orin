from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentos.execution.events import DataClassification
from agentos.providers.catalog import InMemoryModelCatalog
from agentos.providers.models import (
    CancellationRequirement,
    CatalogVersion,
    ConstraintCode,
    FallbackRequest,
    InputKind,
    ModelCost,
    ModelDescriptor,
    ModelPolicyVersion,
    ModelProfile,
    ModelRef,
    ModelRequirements,
    ModelResolutionRequest,
    ModelResolved,
    ModelStatus,
    NoCompatibleModel,
    ProviderDescriptor,
    ProviderOperationContext,
    ProviderRef,
    RegisterModel,
    RegisterProvider,
    ResponseFormat,
    ResolveFallback,
    ProviderErrorCategory,
    ProviderUsage,
    ProviderCost,
    ProviderModelBindingRef,
    ProviderInvocationLimits,
)
from agentos.execution.models import CancellationReason, CancellationReasonCode
from agentos.providers.resolver import ModelResolverService


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def ctx():
    return ProviderOperationContext("user-1", "workspace-1", "agent-1", "execution-1", "resolver-test", "resolver", "actor-1")


def setup_catalog():
    catalog = InMemoryModelCatalog()
    catalog.register_provider(RegisterProvider("request:p", ctx(), 0, "key:p", ProviderDescriptor(ProviderRef("provider:1"), "provider", supported_data_classifications=(DataClassification.INTERNAL,))))
    catalog.register_model(RegisterModel("request:m1", ctx(), 1, "key:m1", ModelDescriptor(ModelRef("model:1"), ProviderRef("provider:1"), "model-1", provider_binding_ref=ProviderModelBindingRef("binding:1"), cost=ModelCost("USD", Decimal("1"), Decimal("2")), profiles=(ModelProfile.BALANCED,))))
    catalog.register_model(RegisterModel("request:m2", ctx(), 2, "key:m2", ModelDescriptor(ModelRef("model:2"), ProviderRef("provider:1"), "model-2", provider_binding_ref=ProviderModelBindingRef("binding:2"), cost=ModelCost("USD", Decimal("2"), Decimal("3")), profiles=(ModelProfile.BALANCED,))))
    return catalog


def requirements(**changes):
    base = dict(context=ctx(), requested_profile=ModelProfile.BALANCED, input_kinds=(InputKind.TEXT,), response_format=ResponseFormat.TEXT, cancellation_requirement=CancellationRequirement.ANY, minimum_context_tokens=10, maximum_input_tokens=100, maximum_output_tokens=20, maximum_total_tokens=120, data_classification=DataClassification.INTERNAL, fallback=FallbackRequest(mode="EXPLICIT_ORDER", ordered_model_refs=(ModelRef("model:2"),), maximum_attempts=1))
    base.update(changes)
    return ModelRequirements(**base)


def request(req):
    return ModelResolutionRequest("resolve:1", req, "resolve-key")


def test_hard_constraints_reject_before_preference_score():
    resolver = ModelResolverService(setup_catalog(), clock=lambda: NOW)
    result = resolver.resolve(request(requirements(data_classification=DataClassification.CONFIDENTIAL, preferred_model_ref=ModelRef("model:1"))))
    assert isinstance(result, NoCompatibleModel)
    assert any(rejection.code is ConstraintCode.DATA_CLASSIFICATION for rejection in result.considered)
    assert "secret" not in repr(result).lower()


def test_same_snapshot_resolves_deterministically():
    resolver = ModelResolverService(setup_catalog(), clock=lambda: NOW)
    first = resolver.resolve(request(requirements()))
    second = resolver.resolve(request(requirements()))
    assert first == second
    assert isinstance(first, ModelResolved)


def test_unknown_cost_cannot_satisfy_required_budget():
    catalog = setup_catalog()
    catalog.register_model(RegisterModel("request:m3", ctx(), 3, "key:m3", ModelDescriptor(ModelRef("model:3"), ProviderRef("provider:1"), "model-3", cost=ModelCost("USD"), profiles=(ModelProfile.BALANCED,))))
    resolver = ModelResolverService(catalog, clock=lambda: NOW)
    result = resolver.resolve(request(requirements(allowed_model_refs=(ModelRef("model:3"),), maximum_cost=Decimal("1"))))
    assert isinstance(result, NoCompatibleModel)
    assert any(rejection.code is ConstraintCode.COST_UNKNOWN for rejection in result.considered)


def test_fallback_is_materialized_and_never_broadens_scope():
    resolver = ModelResolverService(setup_catalog(), clock=lambda: NOW)
    primary = resolver.resolve(request(requirements(fallback=FallbackRequest(mode="EXPLICIT_ORDER", ordered_model_refs=(ModelRef("model:2"),), maximum_attempts=2))))
    assert isinstance(primary, ModelResolved)
    fallback = resolver.resolve_fallback(ResolveFallback("fallback:1", ctx(), primary.selection.selection_ref, primary.selection.primary.model_ref, ProviderErrorCategory.TIMEOUT, ProviderUsage(), ProviderCost(), ProviderInvocationLimits(100, 20, 120), "cancel:none", "fallback-key"))
    assert isinstance(fallback, ModelResolved)
    assert fallback.selection.context == primary.selection.context
    assert fallback.selection.primary.model_ref in {model.model_ref for model in primary.selection.fallbacks}
