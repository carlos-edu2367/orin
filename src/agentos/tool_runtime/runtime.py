from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from agentos.resources.models import (
    AuthorizeResourceOperation, ReleaseResourceLease, ResourceError, ResourceLeaseRequest,
    ResourceOperationContext,
)

from .models import (
    AtomicToolCall, CancellationSignal, EffectState, ResourceUsage, Retryability,
    SensitiveOperationContext, StreamDisposition, ToolError, ToolErrorCode, ToolInvocationOutcome,
    ToolInvocationRequest, ToolInvocationSnapshot, ToolInvocationState, ToolOutboxEntry,
    ToolStreamItem, ToolStreamSink, ToolSucceeded, ToolFailed, ToolCancelled,
)


class ToolAuthorizationPolicy:
    def authorize(self, context: SensitiveOperationContext, descriptor, arguments) -> bool:
        return bool(context.user_id and context.agent_id and context.execution_id and context.purpose)


class ToolRuntimeService:
    def __init__(self, registry, resource_manager, *, authorization=None, clock=None, sink=None) -> None:
        self.registry = registry
        self.resource_manager = resource_manager
        self.authorization = authorization or ToolAuthorizationPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._snapshots: dict[str, ToolInvocationSnapshot] = {}
        self._request_keys: dict[tuple[tuple[str, ...], object, str], tuple[str, str]] = {}
        self.outbox: list[ToolOutboxEntry] = []
        self._sink = sink
        self._signals: dict[str, CancellationSignal] = {}
        self._lock = RLock()

    def _now(self): return self._clock()

    @staticmethod
    def _resource_context(context):
        return ResourceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, context.purpose, context.actor)

    @staticmethod
    def _serialized_size(value) -> int:
        return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _schema(value, schema) -> bool:
        kind = schema["type"]
        if kind == "object":
            if not isinstance(value, dict) or set(value) - set(schema.get("properties", {})): return False
            if any(name not in value for name in schema.get("required", ())): return False
            return all(ToolRuntimeService._schema(value[name], child) for name, child in schema.get("properties", {}).items() if name in value)
        if kind == "array": return isinstance(value, list) and all(ToolRuntimeService._schema(item, schema["items"]) for item in value)
        return {"string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool), "number": isinstance(value, (int, float)) and not isinstance(value, bool), "boolean": isinstance(value, bool), "null": value is None}[kind]

    def _entry(self, event_type, snapshot, **payload):
        entry = ToolOutboxEntry(event_type, snapshot.invocation_id, snapshot.tool_ref, snapshot.context.correlation_id, payload, self._now(), snapshot.context)
        self.outbox.append(entry)
        if self._sink is not None:
            self._sink(entry)

    def _save(self, snapshot, event_type=None, **payload):
        self._snapshots[snapshot.invocation_id] = snapshot
        if event_type: self._entry(event_type, snapshot, **payload)
        return snapshot

    def _failure(self, request, code, reason, *, retryability=Retryability.NEVER, effect_state=EffectState.NOT_APPLIED):
        outcome = ToolFailed(request.invocation_id, ToolError(code, retryability, effect_state, reason))
        snapshot = ToolInvocationSnapshot(request.invocation_id, request.tool_ref, request.context, ToolInvocationState.FAILED, outcome, finished_at=self._now())
        self._save(snapshot, "ToolFinished", outcome="FAILED", error_code=outcome.error.code.value, effect_state=effect_state.value)
        return outcome

    def _limits(self, request, descriptor):
        limited = request.requested_limits
        if limited.timeout is not None and limited.timeout > descriptor.limits.timeout: return False
        if limited.maximum_output_bytes is not None and limited.maximum_output_bytes > descriptor.limits.maximum_output_bytes: return False
        if limited.maximum_stream_items is not None and limited.maximum_stream_items > descriptor.limits.maximum_stream_items: return False
        return True

    def invoke(self, request: ToolInvocationRequest) -> ToolInvocationOutcome:
        return self._execute(request, None)

    def stream(self, request: ToolInvocationRequest, sink: ToolStreamSink) -> ToolInvocationOutcome:
        return self._execute(request, sink)

    def _execute(self, request, sink):
        with self._lock:
            prior = self._snapshots.get(request.invocation_id)
            if prior is not None:
                if prior.context != request.context or prior.tool_ref != request.tool_ref: return self._failure(request, ToolErrorCode.IDEMPOTENCY_CONFLICT, "invocation identity is already bound")
                if prior.outcome is not None: return prior.outcome
            initial = ToolInvocationSnapshot(request.invocation_id, request.tool_ref, request.context, ToolInvocationState.REQUESTED, None)
            self._save(initial)
            try: descriptor = self.registry.resolve(request.tool_ref, request.context)
            except LookupError: return self._failure(request, ToolErrorCode.NOT_FOUND, "tool version is not registered")
            except PermissionError: return self._failure(request, ToolErrorCode.DISABLED, "tool version is disabled")
            if descriptor.idempotency.value == "IDEMPOTENT_WITH_KEY" and request.idempotency_key is None:
                return self._failure(request, ToolErrorCode.INVALID_INPUT, "idempotency key is required")
            fingerprint = json.dumps(request.arguments, sort_keys=True, default=str)
            if request.idempotency_key is not None:
                idem_key = (request.context.scope_key(), request.tool_ref, request.idempotency_key)
                known = self._request_keys.get(idem_key)
                if known is not None:
                    if known[1] != fingerprint: return self._failure(request, ToolErrorCode.IDEMPOTENCY_CONFLICT, "idempotency key has different arguments")
                    return self._snapshots[known[0]].outcome
                self._request_keys[idem_key] = (request.invocation_id, fingerprint)
            if self._serialized_size(request.arguments) > descriptor.limits.maximum_input_bytes or not self._schema(dict(request.arguments), descriptor.input_schema):
                return self._failure(request, ToolErrorCode.INVALID_INPUT, "tool arguments do not satisfy closed input schema")
            if not self._limits(request, descriptor): return self._failure(request, ToolErrorCode.LIMIT_EXCEEDED, "requested limits exceed tool limits")
            if not self.authorization.authorize(request.context, descriptor, request.arguments): return self._failure(request, ToolErrorCode.UNAUTHORIZED, "tool authorization was denied")
            self._save(replace(initial, state=ToolInvocationState.AUTHORIZED))
            resource_context = self._resource_context(request.context)
            leases, handles = [], []
            for requirement in descriptor.resource_requirements:
                lease = self.resource_manager.acquire(ResourceLeaseRequest(
                    f"tool-lease:{request.invocation_id}:{requirement.resource_type.value}", resource_context, requirement.resource_type,
                    requirement.capabilities, requirement.capabilities, requirement.budget, descriptor.limits.timeout,
                    f"tool:{request.invocation_id}:{requirement.resource_type.value}",
                ))
                if isinstance(lease, ResourceError):
                    self._release(resource_context, leases, request.invocation_id)
                    return self._failure(request, ToolErrorCode.RESOURCE_UNAVAILABLE, lease.reason, retryability=lease.retryability, effect_state=lease.effect_state)
                leases.append(lease)
                for capability in requirement.capabilities:
                    handle = self.resource_manager.authorize(AuthorizeResourceOperation(lease.lease_id, f"tool:{request.invocation_id}:{capability.value}", resource_context, capability))
                    if isinstance(handle, ResourceError):
                        self._release(resource_context, leases, request.invocation_id)
                        return self._failure(request, ToolErrorCode.RESOURCE_UNAVAILABLE, handle.reason, retryability=handle.retryability, effect_state=handle.effect_state)
                    handles.append(handle)
            signal = CancellationSignal(); self._signals[request.invocation_id] = signal
            running = ToolInvocationSnapshot(request.invocation_id, request.tool_ref, request.context, ToolInvocationState.RUNNING, None, tuple(lease.lease_id for lease in leases), self._now())
            self._save(running, "ToolStarted", purpose=request.context.purpose)
        try:
            if signal.requested(): return self._cancel(request, running, leases, "cancel requested before tool execution", sink)
            call = AtomicToolCall(request.invocation_id, request.context, dict(request.arguments), tuple(handles), signal, running.started_at + descriptor.limits.timeout)
            tool = self.registry.tool(request.tool_ref)
            if sink is not None and not descriptor.supports_streaming:
                return self._failure(request, ToolErrorCode.INVALID_INPUT, "tool does not support streaming")
            emitted = 0
            def emit(kind, value):
                nonlocal emitted
                if signal.requested() or emitted >= descriptor.limits.maximum_stream_items:
                    return StreamDisposition.REJECTED
                if not isinstance(value, dict): return StreamDisposition.REJECTED
                emitted += 1
                item = ToolStreamItem(request.invocation_id, emitted, self._now(), kind, value)
                disposition = sink.emit(item) if sink is not None else StreamDisposition.ACCEPTED
                if disposition is StreamDisposition.ACCEPTED:
                    with self._lock: self._entry("ToolProgressed", running, stream_sequence=item.sequence, progress_kind=item.kind)
                return disposition
            result = tool.stream(call, emit) if sink is not None and hasattr(tool, "stream") else tool.execute(call)
            if self._now() > call.deadline:
                outcome = ToolFailed(request.invocation_id, ToolError(ToolErrorCode.TIMEOUT, Retryability.NEVER, EffectState.UNKNOWN, "tool deadline elapsed"))
            elif isinstance(result, (ToolSucceeded, ToolFailed, ToolCancelled)): outcome = result
            elif signal.requested(): return self._cancel(request, running, leases, "cancel requested", sink)
            elif not isinstance(result, dict) or not self._schema(result, descriptor.output_schema) or self._serialized_size(result) > descriptor.limits.maximum_output_bytes:
                outcome = ToolFailed(request.invocation_id, ToolError(ToolErrorCode.INVALID_OUTPUT, reason="tool output does not satisfy closed output schema"))
            else: outcome = ToolSucceeded(request.invocation_id, result, ResourceUsage(operations=max(1, len(handles))))
        except TimeoutError:
            outcome = ToolFailed(request.invocation_id, ToolError(ToolErrorCode.TIMEOUT, Retryability.NEVER, EffectState.UNKNOWN, "tool deadline elapsed"))
        except Exception:
            outcome = ToolFailed(request.invocation_id, ToolError(ToolErrorCode.EXECUTION_FAILED, Retryability.AFTER_RECONCILIATION, EffectState.UNKNOWN, "tool execution failed"))
        finally:
            self._release(resource_context, leases, request.invocation_id)
        with self._lock:
            if isinstance(outcome, ToolSucceeded): state = ToolInvocationState.SUCCEEDED
            elif isinstance(outcome, ToolCancelled): state = ToolInvocationState.CANCELLED
            else: state = ToolInvocationState.FAILED
            terminal = replace(running, state=state, outcome=outcome, finished_at=self._now())
            self._save(terminal, "ToolFinished", outcome=state.value, effect_state=getattr(getattr(outcome, "error", None), "effect_state", getattr(outcome, "effect_state", EffectState.APPLIED)).value)
        if sink is not None: sink.close(outcome)
        return outcome

    def _release(self, context, leases, invocation_id):
        for lease in reversed(leases):
            self.resource_manager.release(ReleaseResourceLease(f"tool-release:{invocation_id}:{lease.lease_id}", lease.lease_id, context, lease.fencing_token, "tool invocation complete", f"tool-release:{invocation_id}:{lease.lease_id}"))

    def _cancel(self, request, snapshot, leases, reason, sink):
        self._release(self._resource_context(request.context), leases, request.invocation_id)
        outcome = ToolCancelled(request.invocation_id, reason)
        with self._lock: self._save(replace(snapshot, state=ToolInvocationState.CANCELLED, outcome=outcome, finished_at=self._now()), "ToolFinished", outcome="CANCELLED", effect_state=EffectState.NOT_APPLIED.value)
        if sink is not None: sink.close(outcome)
        return outcome

    def request_cancel(self, invocation_id, context=None, reason: str = "cancel requested") -> bool:
        from .models import CancelToolInvocation
        if isinstance(invocation_id, CancelToolInvocation):
            request = invocation_id
            invocation_id, context, reason = request.invocation_id, request.context, request.reason
        with self._lock:
            snapshot = self._snapshots.get(invocation_id)
            if snapshot is None or snapshot.context != context: return False
            if snapshot.state.terminal: return snapshot.state is ToolInvocationState.CANCELLED
            self._signals.setdefault(invocation_id, CancellationSignal())._cancel()
            return True

    def inspect(self, invocation_id, context=None) -> ToolInvocationSnapshot:
        from .models import AuthorizedToolInvocationQuery
        if isinstance(invocation_id, AuthorizedToolInvocationQuery):
            query = invocation_id
            invocation_id, context = query.invocation_id, query.context
        snapshot = self._snapshots.get(invocation_id)
        if snapshot is None or snapshot.context != context: raise LookupError("invocation is not authorized in this context")
        return snapshot


__all__ = ["ToolRuntimeService", "ToolAuthorizationPolicy"]
