"""Temporary compatibility bridge for legacy Capability programs.

The production dependency is the concrete ToolRuntime public contract; the old
CapabilityToolPort remains supported only so existing capability tests and
persisted programs can be exercised while callers migrate.
"""
from __future__ import annotations

from datetime import timedelta

from .models import SensitiveOperationContext, ToolInvocationRequest, ToolLimitRequest, ToolRef


class CapabilityToolRuntimeAdapter:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    @staticmethod
    def _context(context):
        return SensitiveOperationContext(str(context.user_id), str(context.workspace_id) if context.workspace_id is not None else None, str(context.agent_id), str(context.execution_id), str(context.correlation_id), str(context.purpose), str(context.actor))

    def invoke(self, request):
        from agentos.capabilities.models import EffectState, ResourceUsage, Retryability
        from agentos.capabilities.ports import ToolCancelled, ToolFailed, ToolSucceeded
        outcome = self.runtime.invoke(ToolInvocationRequest(
            request.invocation_id, ToolRef(str(request.tool_ref.tool_id), int(request.tool_ref.version)), self._context(request.context),
            dict(request.arguments.items), str(request.idempotency_key) if request.idempotency_key is not None else None,
            ToolLimitRequest(timeout=timedelta(seconds=request.limits.timeout_seconds)),
        ))
        if outcome.__class__.__name__ == "ToolSucceeded":
            return ToolSucceeded(outcome.invocation_id, outcome.result_ref or f"tool-result:{outcome.invocation_id}", ResourceUsage(tool_invocations=1, resource_units=outcome.usage.operations))
        if outcome.__class__.__name__ == "ToolCancelled":
            return ToolCancelled(outcome.invocation_id, outcome.reason, outcome.partial_result_ref)
        return ToolFailed(outcome.invocation_id, outcome.error.code.value, Retryability(outcome.error.retryability.value), EffectState(outcome.error.effect_state.value), outcome.result_ref, ResourceUsage(tool_invocations=1))

    def request_cancel(self, request):
        # Legacy cancellation only carries a step identity. The stable generated
        # invocation identifier is the one CapabilityService uses for attempt 1.
        return self.runtime.request_cancel(f"invocation:{request.capability_run_id}:{request.step_id}:1", self._context(request.context), request.reason)

    def reconcile(self, request):
        return self.invoke(request)


__all__ = ["CapabilityToolRuntimeAdapter"]
