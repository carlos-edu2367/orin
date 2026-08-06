from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from agentos.execution.events import DataClassification
from agentos.execution.models import CancellationReason, CancellationReasonCode

from .catalog import InMemoryModelCatalog
from .models import *
from .models import same_scope


_CLASSIFICATION_RANK = {
    DataClassification.INTERNAL: 0,
    DataClassification.CONFIDENTIAL: 1,
    DataClassification.RESTRICTED: 2,
}


class ModelResolverService:
    def __init__(self, catalog, *, clock: Callable[[], datetime], cancellation=None, validity: timedelta = timedelta(minutes=5)) -> None:
        self._catalog = catalog
        self._clock = clock
        self._cancellation = cancellation
        self._validity = validity

    def _cancelled(self, request_or_requirements) -> bool:
        signal = getattr(request_or_requirements, "cancellation", None)
        return bool(self._cancellation is not None and self._cancellation.is_cancelled()) or bool(signal is not None and hasattr(signal, "is_cancelled") and signal.is_cancelled())

    def resolve(self, request: ModelResolutionRequest) -> ModelResolutionOutcome:
        requirements = request.requirements
        if self._cancelled(requirements):
            return ModelResolutionCancelled(CancellationReason(CancellationReasonCode.POLICY_REQUESTED))
        now = self._clock()
        page = self._catalog.list_models(AuthorizedModelListQuery(requirements.context))
        eligible, rejected = self._filter(page.items, requirements)
        if self._cancelled(requirements):
            return ModelResolutionCancelled(CancellationReason(CancellationReasonCode.POLICY_REQUESTED))
        if not eligible:
            return NoCompatibleModel("NO_COMPATIBLE_MODEL", tuple(rejected))

        profile = self._catalog.get_profile(requirements.requested_profile) if requirements.requested_profile else None
        preferred = requirements.preferred_model_ref
        eligible.sort(key=lambda descriptor: self._sort_key(descriptor, requirements, preferred))
        primary = eligible[0]
        fallback_refs = self._fallback_refs(requirements.fallback, eligible, primary)
        selected_primary = self._selected(primary, 1, ModelRole.PRIMARY)
        selected_fallbacks = tuple(self._selected(item, index + 2, ModelRole.FALLBACK) for index, item in enumerate(fallback_refs))
        selection_ref = ModelSelectionRef(f"selection:{request.request_id}")
        selection_id = ModelSelectionId(f"selection-id:{request.request_id}")
        valid_until = now + self._validity
        allowed_models = tuple(item.model_ref for item in (primary, *fallback_refs))
        allowed_providers = tuple(dict.fromkeys(item.provider_ref for item in (primary, *fallback_refs)))
        snapshot = ApprovedModelRequirementsSnapshot(
            approved_requirements_ref=ApprovedModelRequirementsRef(f"approved:{request.request_id}"),
            selection_id=selection_id,
            context=requirements.context,
            data_classification=requirements.data_classification,
            region=requirements.region,
            input_kinds=requirements.input_kinds,
            response_format=requirements.response_format,
            required_capabilities=requirements.required_capabilities,
            cancellation_requirement=requirements.cancellation_requirement,
            minimum_context_tokens=requirements.minimum_context_tokens,
            maximum_input_tokens=requirements.maximum_input_tokens,
            maximum_output_tokens=requirements.maximum_output_tokens,
            maximum_total_tokens=requirements.maximum_total_tokens,
            maximum_cost=requirements.maximum_cost,
            allowed_provider_refs=allowed_providers,
            allowed_model_refs=allowed_models,
            fallback_policy_ref=requirements.fallback.policy_ref,
            catalog_version=CatalogVersion(str(page.catalog_version)),
            policy_version=requirements.policy_version or ModelPolicyVersion("policy:default"),
            issued_at=now,
            valid_until=valid_until,
            integrity_ref=IntegrityRef(f"integrity:{request.request_id}"),
        )
        explanation = SelectionExplanation(
            requested_profile=requirements.requested_profile,
            satisfied_constraints=tuple(code for code in ConstraintCode if not any(rejection.code is code for rejection in rejected)),
            applied_preferences=profile.preferences if profile else (),
            rejected_candidates=tuple(rejected),
            fallback_policy_ref=requirements.fallback.policy_ref,
            warnings=(),
        )
        selection = ModelSelection(
            selection_id=selection_id,
            selection_ref=selection_ref,
            context=requirements.context,
            primary=selected_primary,
            fallbacks=selected_fallbacks,
            catalog_version=snapshot.catalog_version,
            policy_version=snapshot.policy_version,
            profile_revision=profile.revision if profile else None,
            pricing_revisions=tuple(item.cost.pricing_revision for item in (primary, *fallback_refs)),
            approved_requirements_ref=snapshot.approved_requirements_ref,
            availability_snapshot_ref=AvailabilitySnapshotRef(f"availability:{page.catalog_version}"),
            explanation=explanation,
            resolved_at=now,
            valid_until=valid_until,
        )
        self._catalog.record_selection(selection, snapshot)
        return ModelResolved(selection)

    def resolve_fallback(self, request: ResolveFallback) -> ModelResolutionOutcome:
        if self._cancelled(request):
            return ModelResolutionCancelled(CancellationReason(CancellationReasonCode.POLICY_REQUESTED))
        selection = self._catalog.inspect_selection(AuthorizedModelSelectionQuery(request.context, request.prior_selection_ref))
        if selection.valid_until <= self._clock():
            return NoCompatibleModel("SELECTION_EXPIRED", ())
        if request.failure_category not in {
            ProviderErrorCategory.TIMEOUT, ProviderErrorCategory.CONNECTION,
            ProviderErrorCategory.RATE_LIMITED, ProviderErrorCategory.MODEL_UNAVAILABLE,
            ProviderErrorCategory.PROVIDER_INTERNAL, ProviderErrorCategory.INDETERMINATE,
        }:
            return NoCompatibleModel("FALLBACK_NOT_ALLOWED_FOR_FAILURE", ())
        remaining = [candidate for candidate in selection.fallbacks if candidate.model_ref != request.failed_model_ref]
        if not remaining:
            return NoCompatibleModel("NO_MATERIALIZED_FALLBACK", ())
        candidate = remaining[0]
        descriptor = self._catalog.get_model(AuthorizedModelQuery(request.context, candidate.model_ref))
        if descriptor.status in {ModelStatus.DISABLED, ModelStatus.RETIRED}:
            return NoCompatibleModel("FALLBACK_MODEL_UNAVAILABLE", (CandidateRejection(candidate.model_ref, ConstraintCode.MODEL_STATUS, "status not eligible"),))
        if request.remaining_limits.maximum_cost is not None and candidate.estimated_cost_ceiling is not None and candidate.estimated_cost_ceiling > request.remaining_limits.maximum_cost:
            return NoCompatibleModel("FALLBACK_BUDGET_EXCEEDED", (CandidateRejection(candidate.model_ref, ConstraintCode.BUDGET, "remaining budget"),))
        now = self._clock()
        new_ref = ModelSelectionRef(f"selection:{request.request_id}")
        new_id = ModelSelectionId(f"selection-id:{request.request_id}")
        valid_until = min(selection.valid_until, now + self._validity)
        snapshot = self._catalog.load_snapshot(selection.approved_requirements_ref, request.context)
        snapshot = replace(snapshot, approved_requirements_ref=ApprovedModelRequirementsRef(f"approved:{request.request_id}"), selection_id=new_id, issued_at=now, valid_until=valid_until, integrity_ref=IntegrityRef(f"integrity:{request.request_id}"))
        new_selection = replace(
            selection,
            selection_id=new_id,
            selection_ref=new_ref,
            primary=replace(candidate, role=ModelRole.PRIMARY, rank=1),
            fallbacks=tuple(replace(item, rank=index + 2) for index, item in enumerate(remaining[1:])),
            approved_requirements_ref=snapshot.approved_requirements_ref,
            resolved_at=now,
            valid_until=valid_until,
        )
        self._catalog.record_selection(new_selection, snapshot)
        return ModelResolved(new_selection)

    def _filter(self, descriptors, requirements):
        eligible = []
        rejected = []
        for descriptor in descriptors:
            code = self._rejection(descriptor, requirements)
            if code is None:
                eligible.append(descriptor)
            else:
                rejected.append(CandidateRejection(descriptor.model_ref, code, code.value))
        return eligible, rejected

    def _rejection(self, descriptor: ModelDescriptor, requirements: ModelRequirements) -> ConstraintCode | None:
        provider = self._catalog.get_provider(descriptor.provider_ref)
        if provider is None:
            return ConstraintCode.PROVIDER_STATUS
        if provider.status in {ProviderStatus.DISABLED, ProviderStatus.RETIRED}:
            return ConstraintCode.PROVIDER_STATUS
        if descriptor.status in {ModelStatus.DISABLED, ModelStatus.RETIRED}:
            return ConstraintCode.MODEL_STATUS
        if requirements.allowed_provider_refs and descriptor.provider_ref not in requirements.allowed_provider_refs:
            return ConstraintCode.PROVIDER_NOT_ALLOWED
        if descriptor.provider_ref in requirements.denied_provider_refs:
            return ConstraintCode.PROVIDER_DENIED
        if requirements.allowed_model_refs and descriptor.model_ref not in requirements.allowed_model_refs:
            return ConstraintCode.MODEL_NOT_ALLOWED
        if _CLASSIFICATION_RANK[requirements.data_classification] > _CLASSIFICATION_RANK[descriptor.compatibility.maximum_data_classification]:
            return ConstraintCode.DATA_CLASSIFICATION
        if provider.supported_data_classifications and requirements.data_classification not in provider.supported_data_classifications:
            return ConstraintCode.DATA_CLASSIFICATION
        if requirements.region and provider.allowed_regions and requirements.region not in provider.allowed_regions:
            return ConstraintCode.REGION
        if requirements.minimum_context_tokens > descriptor.context.maximum_total_tokens:
            return ConstraintCode.CONTEXT_LIMIT
        if requirements.maximum_input_tokens > descriptor.context.maximum_input_tokens or requirements.maximum_output_tokens > descriptor.context.maximum_output_tokens or requirements.maximum_total_tokens > descriptor.context.maximum_total_tokens:
            return ConstraintCode.CONTEXT_LIMIT
        if any(kind not in descriptor.compatibility.supported_input_kinds for kind in requirements.input_kinds):
            return ConstraintCode.INPUT_KIND
        if requirements.response_format not in descriptor.compatibility.supported_response_formats:
            return ConstraintCode.RESPONSE_FORMAT
        for capability in requirements.required_capabilities:
            if capability is RequiredCapability.VISION and not descriptor.vision.supported:
                return ConstraintCode.CAPABILITY
            if capability is RequiredCapability.TOOLS and not descriptor.tools.supported:
                return ConstraintCode.CAPABILITY
            if capability is RequiredCapability.STREAMING and not descriptor.streaming.supported:
                return ConstraintCode.CAPABILITY
            if capability is RequiredCapability.STRUCTURED_OUTPUT and requirements.response_format is ResponseFormat.TEXT:
                return ConstraintCode.CAPABILITY
        if requirements.cancellation_requirement is CancellationRequirement.REMOTE_REQUIRED and descriptor.cancellation.mode is not CancellationMode.COOPERATIVE_REMOTE:
            return ConstraintCode.CANCELLATION
        if requirements.maximum_cost is not None:
            if not descriptor.cost.comparable:
                return ConstraintCode.COST_UNKNOWN
            estimated = (requirements.maximum_input_tokens * descriptor.cost.input_per_million_tokens + requirements.maximum_output_tokens * descriptor.cost.output_per_million_tokens) / Decimal(1_000_000)
            if estimated > requirements.maximum_cost:
                return ConstraintCode.BUDGET
        return None

    @staticmethod
    def _sort_key(descriptor, requirements, preferred):
        estimated = Decimal("Infinity")
        if descriptor.cost.comparable:
            estimated = descriptor.cost.input_per_million_tokens + descriptor.cost.output_per_million_tokens
        return (0 if preferred is not None and descriptor.model_ref == preferred else 1, estimated, str(descriptor.model_ref))

    @staticmethod
    def _fallback_refs(fallback, eligible, primary):
        refs = []
        ordered = tuple(fallback.ordered_model_refs) if fallback.mode in {FallbackMode.EXPLICIT_ORDER, "EXPLICIT_ORDER"} else ()
        by_ref = {item.model_ref: item for item in eligible}
        for ref in ordered:
            item = by_ref.get(ref)
            if item is not None and item.model_ref != primary.model_ref and item not in refs:
                refs.append(item)
        return refs[: max(0, fallback.maximum_attempts - 1)]

    @staticmethod
    def _selected(descriptor, rank, role):
        return SelectedModel(descriptor.model_ref, descriptor.provider_ref, descriptor.provider_binding_ref, descriptor.revision, ResolvedCapabilities((), descriptor.compatibility.supported_input_kinds, descriptor.compatibility.supported_response_formats, descriptor.cancellation.mode), None if not descriptor.cost.comparable else Decimal("1"), rank, role)


__all__ = ["ModelResolverService"]
