from __future__ import annotations

from decimal import Decimal
from datetime import timedelta

from agentos.execution.models import CancellationReason, CancellationReasonCode
from agentos.runtime.models import (
    ModelResolveRequest as RuntimeModelResolveRequest,
    ModelSelection as RuntimeModelSelection,
    ProviderCancelled,
    ProviderFailed,
    ProviderIndeterminate,
    ProviderFinal,
    ProviderToolRequest,
    ProviderUserInputRequest,
    RuntimeErrorCategory,
    RuntimeErrorInfo,
    RuntimeUsage,
)

from .models import *
from .models import same_scope


class RuntimeModelResolverAdapter:
    """Keeps Runtime's reference-only resolver surface over the canonical resolver."""

    def __init__(self, resolver) -> None:
        self._resolver = resolver

    def resolve(self, request: RuntimeModelResolveRequest) -> RuntimeModelSelection:
        requirements = ModelRequirements(
            context=ProviderOperationContext(
                request.context.user_id, request.context.workspace_id, request.context.agent_id,
                request.context.execution_id, request.context.correlation_id, request.context.purpose,
                request.context.actor_ref,
            ),
            maximum_input_tokens=3072,
            maximum_output_tokens=1024,
            maximum_total_tokens=4096,
        )
        outcome = self._resolver.resolve(ModelResolutionRequest(f"{request.requirements_ref}", requirements, f"{request.requirements_ref}:key"))
        if not isinstance(outcome, ModelResolved):
            raise RuntimeError(getattr(outcome, "error", "MODEL_RESOLUTION_CANCELLED"))
        return RuntimeModelSelection(
            selection_ref=outcome.selection.selection_ref,
            approved_requirements_ref=outcome.selection.approved_requirements_ref,
            canonical_selection=outcome.selection,
            approved_requirements=self._resolver.load_snapshot(
                outcome.selection.approved_requirements_ref,
                requirements.context,
            ),
        )


class RuntimeProviderAdapter:
    """Maps the legacy Runtime request/outcomes to the complete Provider port."""

    def __init__(self, provider) -> None:
        self._provider = provider

    def generate(self, request):
        selection = request.selection.canonical_selection
        snapshot = request.selection.approved_requirements
        if selection is None or snapshot is None:
            raise ProviderContractError(ProviderErrorCategory.POLICY_REJECTED, "MISSING_APPROVED_SNAPSHOT")
        context = ProviderOperationContext(
            request.context.user_id, request.context.workspace_id, request.context.agent_id,
            request.context.execution_id, request.context.correlation_id, request.context.purpose,
            request.context.actor_ref,
        )
        maximum_input = 4096
        maximum_output = 1024
        maximum_total = maximum_input + maximum_output
        if request.limits.max_provider_tokens is not None:
            maximum_total = max(1, request.limits.max_provider_tokens)
        canonical_request = ProviderInvocationRequest(
            invocation_id=ProviderInvocationId(str(request.invocation_ref)),
            context=context,
            selection=selection,
            approved_requirements_ref=snapshot.approved_requirements_ref,
            approved_requirements=snapshot,
            limits=ProviderInvocationLimits(maximum_input, maximum_output, maximum_total, request.limits.max_cost),
            cancellation=CancellationSignalRef("cancel:none"),
            idempotency_key=request.idempotency_key,
        )
        outcome = self._provider.generate(canonical_request)
        return self._map_outcome(outcome, request.invocation_ref)

    def stream(self, request):
        """Expose the canonical streaming port to the agentic runtime.

        The legacy adapter remains a mapper for ``generate``; stream requests
        are already canonical and therefore must not be converted to the
        smaller legacy outcome DTOs.
        """
        stream = self._provider.open_stream(request)
        if not hasattr(self._provider, "read_stream"):
            return stream

        def events():
            after = 0
            for _ in range(128):
                batch = self._provider.read_stream(
                    ReadProviderStream(request.context, stream.stream_id, after, 128, timedelta(milliseconds=50))
                )
                if not batch:
                    return
                for event in batch:
                    after = max(after, int(getattr(event, "sequence", after)))
                    yield event
                    if type(event).__name__ in {"StreamCompleted", "StreamFailed", "StreamCancelled"}:
                        return

        return events()

    def read_stream(self, request):
        return self._provider.read_stream(request)

    def cancel(self, request):
        return self._provider.cancel(request)

    @staticmethod
    def _usage(usage: ProviderUsage, cost: ProviderCost) -> RuntimeUsage:
        tokens = usage.total_tokens
        if tokens is None and usage.input_tokens is not None and usage.output_tokens is not None:
            tokens = usage.input_tokens + usage.output_tokens
        return RuntimeUsage(iterations=1, provider_tokens=tokens or 0, cost=cost.amount or Decimal("0"))

    @classmethod
    def _map_outcome(cls, outcome, invocation_ref):
        usage = cls._usage(outcome.usage, outcome.cost)
        if isinstance(outcome, GenerationSucceeded):
            return ProviderFinal(f"result:{outcome.invocation_id}", usage)
        if isinstance(outcome, ToolCallsRequested):
            call = outcome.calls[0]
            return ProviderToolRequest(f"action:{call.tool_call_id}", str(invocation_ref), usage)
        if isinstance(outcome, UserInputRequested):
            return ProviderUserInputRequest(str(outcome.input_request_ref), usage)
        if isinstance(outcome, GenerationCancelled):
            return ProviderCancelled(outcome.reason, usage)
        if isinstance(outcome, GenerationIndeterminate):
            return ProviderIndeterminate(RuntimeErrorInfo(RuntimeErrorCategory.RECONCILIATION, str(outcome.error.code), Retryability.POLICY_DEPENDENT), usage)
        if isinstance(outcome, GenerationFailed):
            category = RuntimeErrorCategory.PROVIDER_TIMEOUT if outcome.error.category is ProviderErrorCategory.TIMEOUT else RuntimeErrorCategory.PROVIDER
            retryability = Retryability(outcome.error.retryability.value)
            return ProviderFailed(RuntimeErrorInfo(category, str(outcome.error.code), retryability), usage)
        raise RuntimeError("UNKNOWN_PROVIDER_OUTCOME")


__all__ = ["RuntimeModelResolverAdapter", "RuntimeProviderAdapter"]
