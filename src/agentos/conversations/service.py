from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.providers.models import (
    AuthorizedModelListQuery,
    CancellationRequirement,
    FallbackRequest,
    InputKind,
    ModelRequirements,
    ModelResolutionRequest,
    ModelResolved,
    ProviderOperationContext,
    ResponseFormat,
)


@dataclass(frozen=True, slots=True)
class ConversationReceipt:
    conversation_id: str
    agent_id: str
    execution_id: str
    state_version: int


class ConversationService:
    def __init__(self, catalog, prompts, agents, executions, resolver=None) -> None:
        self._catalog = catalog
        self._prompts = prompts
        self._agents = agents
        self._executions = executions
        self._resolver = resolver

    def create(self, context: ProviderCatalogContext, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str) -> ConversationReceipt:
        if not isinstance(message, str) or not message.strip() or len(message) > 16_000:
            raise ValueError("message must be a bounded non-blank string")
        record = next((item for item in self._catalog.list(context, provider) if item.model_id == model_id), None)
        if record is None:
            raise LookupError("model is not authorized for this conversation")
        digest = sha256(f"{context.user_id}|{idempotency_key}".encode()).hexdigest()
        conversation_id, agent_id, execution_id = f"conv_{digest}", f"agent_{digest}", f"exe_{digest}"
        task_ref = self._prompts.save(context.user_id, message)
        selection = self._resolve_selection(context, provider, model_id, record, agent_id, execution_id)
        self._agents.configure(context, agent_id, 1, provider, model_id, selection)
        result = self._executions.create({
            "operation_id": f"op_{digest}",
            "context": {"user_id": context.user_id, "workspace_id": workspace_id, "agent_id": agent_id, "execution_id": execution_id, "correlation_id": f"corr_{execution_id}", "purpose": "conversation.create"},
            "task_ref": task_ref, "limits": {}, "expected_agent_version": 1, "idempotency_key": idempotency_key, "requested_at": datetime.now(UTC),
        })
        return ConversationReceipt(conversation_id, agent_id, str(result["execution_id"]), int(result["state_version"]))

    def _resolve_selection(self, context, provider, model_id, record, agent_id, execution_id):
        if self._resolver is None:
            return None
        operation = ProviderOperationContext(
            context.user_id, None, agent_id, execution_id, f"corr_{execution_id}", "conversation.create", "api-gateway",
        )
        descriptor = next((item for item in self._resolver._catalog.list_models(AuthorizedModelListQuery(operation)).items
                           if str(item.provider_ref).endswith(f":{provider}") and str(item.model_ref).endswith(f":{provider}:{model_id}")), None)
        if descriptor is None:
            raise LookupError("model is not authorized for this conversation")
        maximum_total = descriptor.context.maximum_total_tokens
        maximum_output = min(1024, max(1, maximum_total // 2))
        maximum_input = min(4096, maximum_total - maximum_output)
        outcome = self._resolver.resolve(ModelResolutionRequest(
            f"conversation-model:{execution_id}",
            ModelRequirements(
                context=operation, preferred_model_ref=descriptor.model_ref,
                allowed_provider_refs=(descriptor.provider_ref,), allowed_model_refs=(descriptor.model_ref,),
                input_kinds=(InputKind.TEXT,), response_format=ResponseFormat.TEXT,
                cancellation_requirement=CancellationRequirement.ANY, minimum_context_tokens=1,
                maximum_input_tokens=maximum_input, maximum_output_tokens=maximum_output,
                maximum_total_tokens=maximum_total, fallback=FallbackRequest(),
            ),
            f"conversation-model:{execution_id}",
        ))
        if not isinstance(outcome, ModelResolved):
            raise LookupError("model cannot satisfy this conversation")
        return outcome.selection
