from __future__ import annotations

from decimal import Decimal
from datetime import UTC, datetime
from pathlib import Path

from agentos.execution.events import DataClassification
from agentos.runtime.models import ModelResolveRequest as RuntimeModelResolveRequest, OperationContext, ProviderRequest as RuntimeProviderRequest, RuntimeLimits
from agentos.providers.models import GenerationFailed, ProviderCost, ProviderError, ProviderErrorCategory, ProviderInvocationId, ProviderOperationContext, ProviderUsage, Retryability
from agentos.providers.compat import RuntimeModelResolverAdapter, RuntimeProviderAdapter
from agentos.providers.resolver import ModelResolverService
from agentos.providers.catalog import InMemoryModelCatalog
from agentos.providers.models import ModelDescriptor, ModelRef, ModelProfile, ModelCost, ProviderDescriptor, ProviderRef, ProviderModelBindingRef, RegisterProvider, RegisterModel


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def ctx():
    return ProviderOperationContext("user-1", "workspace-1", "agent-1", "execution-1", "resolver-test", "resolver", "actor-1")


def setup_catalog():
    catalog = InMemoryModelCatalog()
    catalog.register_provider(RegisterProvider("request:p", ctx(), 0, "key:p", ProviderDescriptor(ProviderRef("provider:1"), "provider", supported_data_classifications=(DataClassification.INTERNAL,))))
    catalog.register_model(RegisterModel("request:m1", ctx(), 1, "key:m1", ModelDescriptor(ModelRef("model:1"), ProviderRef("provider:1"), "model-1", provider_binding_ref=ProviderModelBindingRef("binding:1"), cost=ModelCost("USD", Decimal("1"), Decimal("2")), profiles=(ModelProfile.BALANCED,))))
    catalog.register_model(RegisterModel("request:m2", ctx(), 2, "key:m2", ModelDescriptor(ModelRef("model:2"), ProviderRef("provider:1"), "model-2", provider_binding_ref=ProviderModelBindingRef("binding:2"), cost=ModelCost("USD", Decimal("2"), Decimal("3")), profiles=(ModelProfile.BALANCED,))))
    return catalog


class FakeProvider:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return GenerationFailed(ProviderInvocationId("invocation:1"), ProviderError(ProviderErrorCategory.TIMEOUT, "TIMEOUT", "safe", Retryability.SAFE), ProviderUsage(), ProviderCost())


def runtime_context():
    return OperationContext("user-1", "workspace-1", "agent-1", "execution-1", "correlation-1", "runtime-test", "actor-1")


def test_runtime_model_adapter_preserves_scope_and_returns_reference_only():
    resolver = ModelResolverService(setup_catalog(), clock=lambda: NOW)
    result = RuntimeModelResolverAdapter(resolver).resolve(RuntimeModelResolveRequest(runtime_context(), "requirements:1"))
    assert result.selection_ref == "selection:requirements:1"
    assert result.approved_requirements_ref == "approved:requirements:1"
    assert "binding" not in repr(result).lower()


def test_runtime_model_adapter_does_not_reach_into_resolver_private_storage():
    source = Path("src/agentos/providers/compat.py").read_text(encoding="utf-8")

    assert "._catalog" not in source


def test_runtime_provider_adapter_preserves_context_and_maps_failure():
    resolver = ModelResolverService(setup_catalog(), clock=lambda: NOW)
    model = RuntimeModelResolverAdapter(resolver).resolve(RuntimeModelResolveRequest(runtime_context(), "requirements:1"))
    provider = FakeProvider()
    outcome = RuntimeProviderAdapter(provider).generate(RuntimeProviderRequest(runtime_context(), model, "context:1", "invocation:1", RuntimeLimits(max_provider_tokens=100), "provider-key"))
    assert outcome.error.code == "TIMEOUT"
    assert provider.requests[0].context.execution_id == "execution-1"
