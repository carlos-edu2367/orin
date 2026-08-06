from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentos.execution.events import DataClassification
from agentos.providers.models import (
    ApprovedModelRequirementsSnapshot,
    CancellationRequirement,
    CatalogVersion,
    DataClassification as PublicClassification,
    IntegrityRef,
    ModelCost,
    ModelDescriptor,
    ModelRef,
    ModelRevision,
    ModelSelectionId,
    ModelStatus,
    ProviderRef,
    ProviderOperationContext,
    ApprovedModelRequirementsRef,
    ResponseFormat,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _context() -> ProviderOperationContext:
    return ProviderOperationContext("user-1", "workspace-1", "agent-1", "execution-1", "correlation-1", "catalog-test", "actor-1")


def test_descriptor_revisions_and_approved_snapshot_are_immutable():
    descriptor = ModelDescriptor(ModelRef("model:1"), ProviderRef("provider:1"), "public-model")
    with pytest.raises(FrozenInstanceError):
        descriptor.status = ModelStatus.DISABLED
    snapshot = ApprovedModelRequirementsSnapshot(
        ApprovedModelRequirementsRef("approved:1"), ModelSelectionId("selection:1"), _context(),
        DataClassification.INTERNAL, None, (), ResponseFormat.TEXT, (), CancellationRequirement.ANY,
        1, 100, 20, 120, Decimal("1"), (), (), None, CatalogVersion("catalog:1"),
        "policy:1", NOW, NOW + timedelta(minutes=5), IntegrityRef("integrity:1"),
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.maximum_cost = Decimal("0")


def test_missing_pricing_is_not_zero():
    cost = ModelCost(currency="USD")
    assert cost.input_per_million_tokens is None
    assert cost.comparable is False
