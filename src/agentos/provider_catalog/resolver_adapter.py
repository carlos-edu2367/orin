"""Maps authorized provider-catalog records into ``agentos.providers`` descriptors."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from agentos.providers.models import (
    InputKind,
    Measurement,
    ModelCompatibility,
    ModelContextLimits,
    ModelCost,
    ModelDescriptor,
    ModelRef,
    ModelRevision,
    ProviderModelBindingRef,
    ProviderRef,
    ToolCapability,
    VisionCapability,
)

from .models import ProviderCatalogContext, ProviderModelRecord


class ProviderModelDescriptorAdapter:
    """Read-only bridge; eligibility remains the existing ModelResolver's job."""

    def __init__(self, catalog) -> None:
        self._catalog = catalog

    def get_model(self, context: ProviderCatalogContext, provider: str, model_id: str) -> ModelDescriptor:
        record = next(
            (item for item in self._catalog.list(context, provider) if item.model_id == model_id),
            None,
        )
        if record is None:
            raise LookupError("authorized provider model was not found")
        return _descriptor(record)


def _descriptor(record: ProviderModelRecord) -> ModelDescriptor:
    context_window = record.context_window or 4096
    inputs = [InputKind.TEXT]
    if "image" in record.input_modalities:
        inputs.append(InputKind.IMAGE)
    capabilities = set(record.capabilities)
    return ModelDescriptor(
        model_ref=ModelRef(f"catalog:{record.provider}:{record.model_id}"),
        provider_ref=ProviderRef(f"provider:{record.provider}"),
        name=record.display_name,
        provider_binding_ref=ProviderModelBindingRef(f"binding:{record.provider}:{record.model_id}"),
        context=ModelContextLimits(context_window, context_window, max(1, context_window // 4), "provider-catalog"),
        cost=ModelCost(
            input_per_million_tokens=_decimal(record.pricing.input_per_million) if record.pricing else None,
            output_per_million_tokens=_decimal(record.pricing.output_per_million) if record.pricing else None,
            measurement_basis=Measurement.COMPUTED_FROM_CATALOG,
        ),
        vision=VisionCapability(supported="image" in record.input_modalities),
        tools=ToolCapability(supported="tools" in capabilities or "tool_choice" in capabilities),
        compatibility=ModelCompatibility(supported_input_kinds=tuple(inputs)),
        revision=ModelRevision(f"catalog-revision:{record.refreshed_at.isoformat()}"),
    )


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None
