from __future__ import annotations

from dataclasses import replace

from .models import *
from .models import same_scope


class InMemoryModelCatalog:
    """Deterministic reference catalog; durable adapters implement the same port."""

    def __init__(self) -> None:
        self._version = 0
        self._providers: dict[ProviderRef, ProviderDescriptor] = {}
        self._provider_revisions: dict[ProviderRef, tuple[ProviderDescriptor, ...]] = {}
        self._models: dict[ModelRef, ModelDescriptor] = {}
        self._model_revisions: dict[ModelRef, tuple[ModelDescriptor, ...]] = {}
        self._profiles: dict[ModelProfile, ModelProfileDefinition] = {}
        self._selections: dict[ModelSelectionRef, CatalogSelectionRecord] = {}
        self._idempotency: dict[str, tuple[object, CatalogMutationResult]] = {}

    @property
    def catalog_version(self) -> int:
        return self._version

    def _check(self, request: CatalogMutationRequest) -> CatalogMutationResult | None:
        previous = self._idempotency.get(str(request.idempotency_key))
        if previous is not None:
            if previous[0] != request:
                raise CatalogConflictError("IDEMPOTENCY_PAYLOAD_CONFLICT")
            return previous[1]
        if request.expected_catalog_version != self._version:
            raise CatalogConflictError("CATALOG_VERSION_CONFLICT")
        return None

    def _commit(self, request: CatalogMutationRequest, result: CatalogMutationResult) -> CatalogMutationResult:
        self._version += 1
        committed = replace(result, catalog_version=self._version)
        self._idempotency[str(request.idempotency_key)] = (request, committed)
        return committed

    def register_provider(self, request: RegisterProvider) -> CatalogMutationResult:
        if (existing := self._check(request)) is not None:
            return existing
        descriptor = request.descriptor
        if descriptor.provider_ref in self._providers:
            raise CatalogConflictError("PROVIDER_REVISION_EXISTS")
        self._providers[descriptor.provider_ref] = descriptor
        self._provider_revisions[descriptor.provider_ref] = (descriptor,)
        return self._commit(request, CatalogMutationResult(0, descriptor.provider_ref, descriptor.status))

    def register_model(self, request: RegisterModel) -> CatalogMutationResult:
        if (existing := self._check(request)) is not None:
            return existing
        descriptor = request.descriptor
        provider = self._providers.get(descriptor.provider_ref)
        if provider is None:
            raise CatalogConflictError("PROVIDER_NOT_FOUND")
        if descriptor.provider_ref not in self._providers:
            raise CatalogConflictError("PROVIDER_NOT_FOUND")
        if descriptor.model_ref in self._models:
            raise CatalogConflictError("MODEL_REVISION_EXISTS")
        self._models[descriptor.model_ref] = descriptor
        self._model_revisions[descriptor.model_ref] = (descriptor,)
        return self._commit(request, CatalogMutationResult(0, descriptor.model_ref, descriptor.status))

    def publish_profile(self, request: PublishModelProfile) -> CatalogMutationResult:
        if (existing := self._check(request)) is not None:
            return existing
        current = self._profiles.get(request.profile.profile)
        if current is not None and request.profile.revision == current.revision:
            raise CatalogConflictError("PROFILE_REVISION_EXISTS")
        self._profiles[request.profile.profile] = request.profile
        return self._commit(request, CatalogMutationResult(0, OpaqueRef(str(request.profile.profile)), "PUBLISHED"))

    def publish_pricing(self, request: PublishModelPricing) -> CatalogMutationResult:
        if (existing := self._check(request)) is not None:
            return existing
        descriptor = self._models.get(request.model_ref)
        if descriptor is None:
            raise CatalogConflictError("MODEL_NOT_FOUND")
        updated = replace(descriptor, cost=request.cost, revision=ModelRevision(f"{descriptor.revision}:pricing"))
        self._models[request.model_ref] = updated
        self._model_revisions[request.model_ref] += (updated,)
        return self._commit(request, CatalogMutationResult(0, request.cost.pricing_revision, "PUBLISHED"))

    def change_model_status(self, request: ChangeModelStatus) -> CatalogMutationResult:
        if (existing := self._check(request)) is not None:
            return existing
        descriptor = self._models.get(request.model_ref)
        if descriptor is None:
            raise CatalogConflictError("MODEL_NOT_FOUND")
        if descriptor.status is not request.expected_status:
            raise CatalogConflictError("MODEL_STATUS_CONFLICT")
        if request.new_status is ModelStatus.ACTIVE and descriptor.status in {ModelStatus.DISABLED, ModelStatus.DEPRECATED}:
            if request.new_revision is None:
                raise CatalogConflictError("REACTIVATION_REQUIRES_NEW_REVISION")
        if descriptor.status is ModelStatus.RETIRED:
            raise CatalogConflictError("RETIRED_IS_TERMINAL")
        allowed = {
            ModelStatus.ACTIVE: {ModelStatus.DEPRECATED, ModelStatus.DISABLED},
            ModelStatus.DEPRECATED: {ModelStatus.DISABLED},
            ModelStatus.DISABLED: {ModelStatus.DEPRECATED, ModelStatus.RETIRED},
        }
        if request.new_status not in allowed.get(descriptor.status, set()):
            raise CatalogConflictError("INVALID_MODEL_STATUS_TRANSITION")
        updated = replace(descriptor, status=request.new_status, revision=request.new_revision or ModelRevision(f"{descriptor.revision}:status"))
        self._models[request.model_ref] = updated
        self._model_revisions[request.model_ref] += (updated,)
        return self._commit(request, CatalogMutationResult(0, request.model_ref, request.new_status))

    def change_provider_status(self, request: ChangeProviderStatus) -> CatalogMutationResult:
        if (existing := self._check(request)) is not None:
            return existing
        descriptor = self._providers.get(request.provider_ref)
        if descriptor is None:
            raise CatalogConflictError("PROVIDER_NOT_FOUND")
        if descriptor.status is not request.expected_status:
            raise CatalogConflictError("PROVIDER_STATUS_CONFLICT")
        if descriptor.status is ProviderStatus.RETIRED:
            raise CatalogConflictError("RETIRED_IS_TERMINAL")
        if request.new_status is ProviderStatus.ACTIVE and descriptor.status is ProviderStatus.DISABLED and request.new_revision is None:
            raise CatalogConflictError("REACTIVATION_REQUIRES_NEW_REVISION")
        allowed = {
            ProviderStatus.ACTIVE: {ProviderStatus.DEGRADED, ProviderStatus.DISABLED},
            ProviderStatus.DEGRADED: {ProviderStatus.ACTIVE, ProviderStatus.DISABLED},
            ProviderStatus.DISABLED: {ProviderStatus.RETIRED},
        }
        if request.new_status not in allowed.get(descriptor.status, set()):
            raise CatalogConflictError("INVALID_PROVIDER_STATUS_TRANSITION")
        updated = replace(descriptor, status=request.new_status, revision=request.new_revision or ProviderRevision(f"{descriptor.revision}:status"))
        self._providers[request.provider_ref] = updated
        self._provider_revisions[request.provider_ref] += (updated,)
        return self._commit(request, CatalogMutationResult(0, request.provider_ref, request.new_status))

    def get_model(self, query: AuthorizedModelQuery) -> ModelDescriptor:
        descriptor = self._models.get(query.model_ref)
        if descriptor is None:
            raise KeyError("MODEL_NOT_FOUND")
        if query.revision is not None:
            for revision in self._model_revisions.get(query.model_ref, ()):
                if revision.revision == query.revision:
                    return revision
            raise KeyError("MODEL_REVISION_NOT_FOUND")
        return descriptor

    def list_models(self, query: AuthorizedModelListQuery) -> ModelPage:
        values = tuple(self._models.values())
        if query.providers:
            values = tuple(item for item in values if item.provider_ref in query.providers)
        if query.statuses:
            values = tuple(item for item in values if item.status in query.statuses)
        if query.profiles:
            values = tuple(item for item in values if any(profile in item.profiles for profile in query.profiles))
        return ModelPage(tuple(sorted(values, key=lambda item: str(item.model_ref))), self._version)

    def record_selection(self, selection: ModelSelection, snapshot: ApprovedModelRequirementsSnapshot) -> ModelSelection:
        if not same_scope(selection.context, snapshot.context):
            raise PermissionError("SELECTION_SCOPE_MISMATCH")
        existing = self._selections.get(selection.selection_ref)
        if existing is not None and existing != CatalogSelectionRecord(selection, snapshot):
            raise CatalogConflictError("SELECTION_REF_CONFLICT")
        self._selections[selection.selection_ref] = CatalogSelectionRecord(selection, snapshot)
        return selection

    def inspect_selection(self, query: AuthorizedModelSelectionQuery) -> ModelSelection:
        record = self._selections.get(query.selection_ref)
        if record is None:
            raise KeyError("SELECTION_NOT_FOUND")
        if not same_scope(record.selection.context, query.context):
            raise PermissionError("SELECTION_OWNERSHIP_MISMATCH")
        return record.selection

    def load_snapshot(self, reference: ApprovedModelRequirementsRef, context: ModelCatalogOperationContext) -> ApprovedModelRequirementsSnapshot:
        for record in self._selections.values():
            if record.snapshot.approved_requirements_ref == reference:
                if not same_scope(record.snapshot.context, context):
                    raise PermissionError("SNAPSHOT_OWNERSHIP_MISMATCH")
                return record.snapshot
        raise KeyError("SNAPSHOT_NOT_FOUND")

    def get_profile(self, profile: ModelProfile) -> ModelProfileDefinition | None:
        return self._profiles.get(profile)

    def get_pricing(self, model_ref: ModelRef) -> ModelCost:
        return self.get_model(AuthorizedModelQuery(ModelCatalogOperationContext("catalog", None, "catalog", "catalog", "catalog", "catalog", "catalog"), model_ref)).cost

    def get_provider(self, provider_ref: ProviderRef) -> ProviderDescriptor | None:
        return self._providers.get(provider_ref)


__all__ = ["InMemoryModelCatalog"]
