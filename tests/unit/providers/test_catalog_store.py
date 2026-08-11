from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from agentos.providers.catalog import InMemoryModelCatalog
from agentos.providers.models import (
    ApprovedModelRequirementsRef,
    ApprovedModelRequirementsSnapshot,
    AuthorizedModelSelectionQuery,
    CatalogConflictError,
    ChangeModelStatus,
    ModelDescriptor,
    ModelRef,
    ModelStatus,
    ModelSelection,
    ModelSelectionId,
    ModelSelectionRef,
    ProviderDescriptor,
    ProviderOperationContext,
    ProviderRef,
    RegisterModel,
    RegisterProvider,
    ResponseFormat,
    CancellationRequirement,
    DataClassification,
    CatalogVersion,
    IntegrityRef,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def context(user: str = "user-1"):
    return ProviderOperationContext(user, "workspace-1", "agent-1", "execution-1", "correlation-1", "catalog-test", "actor-1")


def provider_request(expected=0):
    return RegisterProvider("request:provider", context(), expected, "key:provider", ProviderDescriptor(ProviderRef("provider:1"), "provider"))


def model_request(expected=1):
    return RegisterModel("request:model", context(), expected, "key:model", ModelDescriptor(ModelRef("model:1"), ProviderRef("provider:1"), "model"))


def test_register_provider_is_idempotent_for_same_key_and_rejects_version_conflict():
    catalog = InMemoryModelCatalog()
    first = catalog.register_provider(provider_request())
    again = catalog.register_provider(provider_request())
    assert again == first
    with pytest.raises(CatalogConflictError):
        catalog.register_provider(replace(provider_request(), expected_catalog_version=first.catalog_version + 1))


def test_catalog_idempotency_key_is_scoped_to_operation_context():
    catalog = InMemoryModelCatalog()
    first = catalog.register_provider(provider_request())
    other_context_request = replace(
        provider_request(expected=first.catalog_version),
        context=context("user-2"),
        descriptor=replace(
            provider_request().descriptor,
            provider_ref=ProviderRef("provider:2"),
        ),
    )

    second = catalog.register_provider(other_context_request)

    assert second.catalog_version == first.catalog_version + 1
    assert catalog.get_provider(ProviderRef("provider:2")) is not None


def test_status_transitions_are_valid_and_revision_history_is_immutable():
    catalog = InMemoryModelCatalog()
    catalog.register_provider(provider_request())
    catalog.register_model(model_request())
    changed = catalog.change_model_status(ChangeModelStatus("request:disable", context(), 2, "key:disable", ModelRef("model:1"), ModelStatus.ACTIVE, ModelStatus.DISABLED, "maintenance"))
    assert changed.status == ModelStatus.DISABLED
    with pytest.raises(CatalogConflictError):
        catalog.change_model_status(ChangeModelStatus("request:reactivate", context(), 3, "key:reactivate", ModelRef("model:1"), ModelStatus.DISABLED, ModelStatus.ACTIVE, "reactivate"))


def test_selection_is_loaded_only_with_matching_ownership():
    catalog = InMemoryModelCatalog()
    selection = object.__new__(ModelSelection)
    selection = ModelSelection(
        ModelSelectionId("selection:1"), ModelSelectionRef("selection:1"), context(),
        primary=object(), fallbacks=(), catalog_version="catalog:1", policy_version="policy:1",
        profile_revision=None, pricing_revisions=(), approved_requirements_ref="approved:1",
        availability_snapshot_ref="availability:1", explanation=object(), resolved_at=NOW,
        valid_until=NOW + timedelta(minutes=5),
    )
    snapshot = ApprovedModelRequirementsSnapshot(
        ApprovedModelRequirementsRef("approved:1"), ModelSelectionId("selection:1"), context(),
        DataClassification.INTERNAL, None, (), ResponseFormat.TEXT, (), CancellationRequirement.ANY,
        1, 100, 20, 120, None, (), (), None, CatalogVersion("catalog:1"), "policy:1",
        NOW, NOW + timedelta(minutes=5), IntegrityRef("integrity:1"),
    )
    catalog.record_selection(selection, snapshot)
    query = AuthorizedModelSelectionQuery(context(), ModelSelectionRef("selection:1"))
    assert catalog.inspect_selection(query).selection_ref == "selection:1"
    with pytest.raises(PermissionError):
        catalog.inspect_selection(AuthorizedModelSelectionQuery(context("other-user"), ModelSelectionRef("selection:1")))
