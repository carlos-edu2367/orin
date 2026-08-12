"""Durable ``ModelCatalogPort`` facade over authorized provider model cache."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import provider_model_selections
from agentos.providers.models import (
    ApprovedModelRequirementsRef,
    ApprovedModelRequirementsSnapshot,
    AuthorizedModelListQuery,
    AuthorizedModelQuery,
    AuthorizedModelSelectionQuery,
    AvailabilitySnapshotRef,
    CancellationRequirement,
    CatalogVersion,
    FallbackPolicyRef,
    InputKind,
    IntegrityRef,
    ModelPage,
    ProfileRevision,
    ModelRole,
    ModelRef,
    ModelSelection,
    ModelSelectionId,
    ModelSelectionRef,
    ModelRevision,
    ModelPolicyVersion,
    ProviderDescriptor,
    ProviderModelBindingRef,
    ProviderOperationContext,
    ProviderRef,
    ProviderStatus,
    RequiredCapability,
    ResolvedCapabilities,
    ResponseFormat,
    SelectedModel,
    SelectionExplanation,
)
from agentos.execution.events import DataClassification

from .models import ProviderCatalogContext
from .resolver_adapter import _descriptor


class PostgresProviderModelCatalog:
    """ModelResolver-compatible catalog backed by PostgreSQL provider cache.

    It deliberately derives descriptors from user-scoped cached records rather
    than accepting an arbitrary provider/model string from a caller.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._repository = PostgresProviderCatalogRepository(engine)

    @property
    def catalog_version(self) -> int:
        return 1

    def list_models(self, query: AuthorizedModelListQuery) -> ModelPage:
        context = ProviderCatalogContext(str(query.context.user_id), str(query.context.purpose))
        items = []
        for provider in ("openrouter", "openai", "anthropic", "omniroute", "ollama"):
            for record in self._repository.list(context, provider):
                descriptor = _descriptor(record)
                items.append(replace(
                    descriptor,
                    model_ref=ModelRef(f"catalog:{query.context.user_id}:{record.provider}:{record.model_id}"),
                    provider_ref=ProviderRef(f"provider:{query.context.user_id}:{record.provider}"),
                ))
        return ModelPage(tuple(items), self.catalog_version)

    def get_model(self, query: AuthorizedModelQuery):
        return next((item for item in self.list_models(AuthorizedModelListQuery(query.context)).items if item.model_ref == query.model_ref), None)

    def get_provider(self, provider_ref: ProviderRef):
        raw = str(provider_ref)
        if not raw.startswith("provider:") or ":" not in raw[len("provider:"):]:
            return None
        user_id, provider = raw[len("provider:"):].rsplit(":", 1)
        if not user_id or not provider:
            return None
        context = ProviderCatalogContext(user_id, "provider.model.resolve")
        credential = self._repository.credential(context, provider)
        if credential is None:
            return None
        return ProviderDescriptor(provider_ref, provider, ProviderStatus.ACTIVE if credential.get("enabled") is True else ProviderStatus.DISABLED)

    def get_profile(self, _profile):
        return None

    def record_selection(self, selection: ModelSelection, _snapshot) -> ModelSelection:
        context = selection.context
        values = {
            "selection_ref": str(selection.selection_ref), "user_id": str(context.user_id), "agent_id": str(context.agent_id),
            "execution_id": str(context.execution_id), "correlation_id": str(context.correlation_id), "purpose": str(context.purpose),
            "model_ref": str(selection.primary.model_ref), "model_revision": str(selection.primary.descriptor_revision),
            "selection": _selection_json(selection), "requirements": _requirements_json(_snapshot),
            "resolved_at": selection.resolved_at, "valid_until": selection.valid_until,
        }
        with self._engine.begin() as connection:
            existing = connection.execute(select(provider_model_selections).where(provider_model_selections.c.selection_ref == values["selection_ref"])).mappings().first()
            if existing is None:
                connection.execute(insert(provider_model_selections).values(**values))
            elif (
                existing["user_id"] == values["user_id"]
                and existing["agent_id"] == values["agent_id"]
                and existing["execution_id"] == values["execution_id"]
                and existing["correlation_id"] == values["correlation_id"]
                and existing["purpose"] == values["purpose"]
                and existing["model_ref"] == values["model_ref"]
                and existing["model_revision"] == values["model_revision"]
            ):
                connection.execute(update(provider_model_selections).where(
                    provider_model_selections.c.selection_ref == values["selection_ref"],
                    provider_model_selections.c.user_id == values["user_id"],
                    provider_model_selections.c.model_ref == values["model_ref"],
                    provider_model_selections.c.model_revision == values["model_revision"],
                ).values(selection=values["selection"], requirements=values["requirements"]))
            else:
                from agentos.providers.models import CatalogConflictError
                raise CatalogConflictError("model selection reference is immutable")
        return selection

    def inspect_selection(self, query: AuthorizedModelSelectionQuery):
        row = self._selection_row(query.context, str(query.selection_ref))
        selection = row["selection"]
        if not isinstance(selection, dict):
            raise LookupError("selection snapshot is unavailable")
        return ModelSelection(
            ModelSelectionId(selection["selection_id"]), ModelSelectionRef(row["selection_ref"]), query.context,
            _selected_from_json(selection["primary"]), tuple(_selected_from_json(item) for item in selection["fallbacks"]),
            CatalogVersion(selection["catalog_version"]), ModelPolicyVersion(selection["policy_version"]),
            ProfileRevision(selection["profile_revision"]) if selection["profile_revision"] else None,
            tuple(), ApprovedModelRequirementsRef(selection["approved_requirements_ref"]),
            AvailabilitySnapshotRef(selection["availability_snapshot_ref"]), SelectionExplanation(None), row["resolved_at"], row["valid_until"],
        )

    def load_snapshot(self, reference, context):
        with self._engine.connect() as connection:
            row = connection.execute(select(provider_model_selections).where(
                provider_model_selections.c.user_id == str(context.user_id),
                provider_model_selections.c.requirements["approved_requirements_ref"].as_string() == str(reference),
            )).mappings().first()
        if row is None or not isinstance(row["requirements"], dict):
            raise LookupError("selection requirements are unavailable")
        data = row["requirements"]
        return ApprovedModelRequirementsSnapshot(
            ApprovedModelRequirementsRef(data["approved_requirements_ref"]), ModelSelectionId(data["selection_id"]), context,
            DataClassification(data["data_classification"]), data["region"], tuple(InputKind(item) for item in data["input_kinds"]),
            ResponseFormat(data["response_format"]), tuple(RequiredCapability(item) for item in data["required_capabilities"]),
            CancellationRequirement(data["cancellation_requirement"]), data["minimum_context_tokens"], data["maximum_input_tokens"],
            data["maximum_output_tokens"], data["maximum_total_tokens"], Decimal(data["maximum_cost"]) if data["maximum_cost"] is not None else None,
            tuple(ProviderRef(item) for item in data["allowed_provider_refs"]), tuple(ModelRef(item) for item in data["allowed_model_refs"]),
            FallbackPolicyRef(data["fallback_policy_ref"]) if data["fallback_policy_ref"] else None, CatalogVersion(data["catalog_version"]),
            ModelPolicyVersion(data["policy_version"]), row["resolved_at"], row["valid_until"], IntegrityRef(data["integrity_ref"]),
            maximum_fallback_attempts=data["maximum_fallback_attempts"], allow_cross_provider_fallback=data["allow_cross_provider_fallback"],
        )

    def _selection_row(self, context: ProviderOperationContext, selection_ref: str):
        with self._engine.connect() as connection:
            row = connection.execute(select(provider_model_selections).where(
                provider_model_selections.c.selection_ref == selection_ref,
                provider_model_selections.c.user_id == str(context.user_id),
                provider_model_selections.c.agent_id == str(context.agent_id),
                provider_model_selections.c.execution_id == str(context.execution_id),
                provider_model_selections.c.correlation_id == str(context.correlation_id),
                provider_model_selections.c.purpose == str(context.purpose),
            )).mappings().first()
        if row is None:
            raise LookupError("selection is not authorized in this scope")
        return row


def _selected_json(item: SelectedModel) -> dict[str, object]:
    return {"model_ref": str(item.model_ref), "provider_ref": str(item.provider_ref), "binding_ref": str(item.provider_binding_ref), "revision": str(item.descriptor_revision), "cost": str(item.estimated_cost_ceiling) if item.estimated_cost_ceiling is not None else None, "rank": item.rank, "role": item.role.value}


def _selected_from_json(data: dict[str, object]) -> SelectedModel:
    return SelectedModel(ModelRef(str(data["model_ref"])), ProviderRef(str(data["provider_ref"])), ProviderModelBindingRef(str(data["binding_ref"])), ModelRevision(str(data["revision"])), ResolvedCapabilities(), Decimal(str(data["cost"])) if data["cost"] is not None else None, int(data["rank"]), ModelRole(str(data["role"])))


def _selection_json(selection: ModelSelection) -> dict[str, object]:
    return {"selection_id": str(selection.selection_id), "primary": _selected_json(selection.primary), "fallbacks": [_selected_json(item) for item in selection.fallbacks], "catalog_version": str(selection.catalog_version), "policy_version": str(selection.policy_version), "profile_revision": str(selection.profile_revision) if selection.profile_revision else None, "approved_requirements_ref": str(selection.approved_requirements_ref), "availability_snapshot_ref": str(selection.availability_snapshot_ref)}


def _requirements_json(snapshot) -> dict[str, object]:
    return {"approved_requirements_ref": str(snapshot.approved_requirements_ref), "selection_id": str(snapshot.selection_id), "data_classification": snapshot.data_classification.value, "region": snapshot.region, "input_kinds": [item.value for item in snapshot.input_kinds], "response_format": snapshot.response_format.value, "required_capabilities": [item.value for item in snapshot.required_capabilities], "cancellation_requirement": snapshot.cancellation_requirement.value, "minimum_context_tokens": snapshot.minimum_context_tokens, "maximum_input_tokens": snapshot.maximum_input_tokens, "maximum_output_tokens": snapshot.maximum_output_tokens, "maximum_total_tokens": snapshot.maximum_total_tokens, "maximum_cost": str(snapshot.maximum_cost) if snapshot.maximum_cost is not None else None, "allowed_provider_refs": [str(item) for item in snapshot.allowed_provider_refs], "allowed_model_refs": [str(item) for item in snapshot.allowed_model_refs], "fallback_policy_ref": str(snapshot.fallback_policy_ref) if snapshot.fallback_policy_ref else None, "catalog_version": str(snapshot.catalog_version), "policy_version": str(snapshot.policy_version), "integrity_ref": str(snapshot.integrity_ref), "maximum_fallback_attempts": snapshot.maximum_fallback_attempts, "allow_cross_provider_fallback": snapshot.allow_cross_provider_fallback}
