"""Production composition boundary for the tool runtime.

The composition owns policy wiring and public projections. Physical filesystem,
terminal, browser, artifact, and delegation implementations remain injected
ports; this module never creates them or exposes their private handles.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable, Mapping

from .catalog import ActionResult, CatalogDescriptor, ToolCatalog, _policy_allows, default_tool_catalog
from .adapters import sanitize_adapter_result
from .models import (
    ToolCancelled,
    ToolFailed,
    ToolInvocationRequest,
    ToolSucceeded,
    ToolRef,
)
from .registry import InMemoryToolRegistry
from .runtime import ToolRuntimeService


Hook = Callable[..., object]


@dataclass(frozen=True, slots=True)
class RuntimeHooks:
    """Optional seams for policy authorities owned outside this composition."""

    authorization: Hook | None = None
    policy: Hook | None = None
    owner: Hook | None = None
    lease: Hook | None = None
    idempotency: Hook | None = None
    limits: Hook | None = None
    cancellation: Hook | None = None
    audit: Hook | None = None


def _allows(hook: Hook | None, *args: object) -> bool:
    if hook is None:
        return True
    target = hook if callable(hook) else getattr(hook, "allows", None)
    if target is None:
        return False
    try:
        signature = inspect.signature(target)
        parameters = tuple(signature.parameters.values())
        positional = tuple(item for item in parameters if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD))
        if any(item.kind is item.VAR_POSITIONAL for item in parameters):
            decision = target(*args)
        elif sum(item.default is item.empty for item in positional) > len(args) or not positional:
            return False
        else:
            decision = target(*args[:len(positional)])
    except (TypeError, ValueError, AttributeError):
        return False
    except Exception:
        return False
    return decision is True


def _artifact_refs(outcome: object) -> tuple[str, ...]:
    result = getattr(outcome, "result", None)
    public = sanitize_adapter_result(result)
    return tuple(public.get("artifact_refs", ()))


class ProductionToolRuntime:
    """Composed, provider-safe façade over :class:`ToolRuntimeService`."""

    def __init__(
        self,
        *,
        resource_manager=None,
        catalog: ToolCatalog | None = None,
        registry: InMemoryToolRegistry | None = None,
        runtime: ToolRuntimeService | None = None,
        tool_bindings: Mapping[object, object] | None = None,
        hooks: RuntimeHooks | None = None,
        integrity: str = "catalog:agentos.tools",
    ) -> None:
        self.catalog = catalog or default_tool_catalog()
        self.registry = registry or InMemoryToolRegistry()
        self.hooks = hooks or RuntimeHooks()
        if runtime is None:
            if resource_manager is None:
                raise ValueError("resource_manager is required when runtime is not supplied")
            runtime = ToolRuntimeService(self.registry, resource_manager)
        self.runtime = runtime
        if tool_bindings:
            self._register_bindings(tool_bindings, integrity)

    def _register_bindings(self, bindings: Mapping[object, object], integrity: str) -> None:
        for entry in self.catalog.descriptors:
            binding = None
            for key in (entry.tool_ref, entry.tool_ref.tool_id, entry.name):
                if key in bindings:
                    binding = bindings[key]
                    break
            if binding is None:
                continue
            self.registry.register_bootstrap(entry.descriptor, binding, integrity=integrity)

    def _entry(self, tool_ref: ToolRef) -> CatalogDescriptor:
        return self.catalog.resolve(tool_ref)

    def _visible(self, context: object, policy: object, entry: CatalogDescriptor) -> bool:
        if not _policy_allows(policy, context, entry) or not _allows(self.hooks.owner, context, entry):
            return False
        try:
            self.registry.resolve(entry.tool_ref, context)
        except (LookupError, PermissionError):
            return False
        return True

    def provider_tools(self, context: object, policy: object = None):
        """Return only name, description, and closed input schema."""
        return tuple(
            entry.provider_projection()
            for entry in self.catalog.descriptors
            if self._visible(context, policy, entry)
        )

    def _preflight(self, request: ToolInvocationRequest, entry: CatalogDescriptor) -> None:
        arguments = request.arguments
        checks = (
            (self.hooks.owner, (request.context, entry), "owner"),
            (self.hooks.policy, (request.context, entry, arguments), "policy"),
            (self.hooks.authorization, (request.context, entry.descriptor, arguments), "authorization"),
            (self.hooks.idempotency, (request, entry.descriptor), "idempotency"),
            (self.hooks.limits, (request, entry.descriptor), "limits"),
            (self.hooks.lease, (request.context, entry.descriptor, request), "lease"),
        )
        for hook, args, label in checks:
            if not _allows(hook, *args):
                raise PermissionError(f"tool {label} hook denied the operation")

    def invoke(self, request: ToolInvocationRequest) -> ActionResult:
        entry = self._entry(request.tool_ref)
        self._preflight(request, entry)
        if not _allows(self.hooks.cancellation, request):
            result = ActionResult.cancelled(action_id=request.invocation_id, summary="Action cancelled before execution")
            self._audit(result)
            return result
        outcome = self.runtime.invoke(request)
        result = self._project(request.invocation_id, entry, outcome)
        self._audit(result)
        return result

    def _project(self, action_id: str, entry: CatalogDescriptor, outcome: object) -> ActionResult:
        if isinstance(outcome, ToolSucceeded):
            return ActionResult.success(
                action_id=action_id,
                summary=f"{entry.name} completed",
                artifact_refs=_artifact_refs(outcome),
            )
        if isinstance(outcome, ToolCancelled):
            return ActionResult.cancelled(action_id=action_id, summary="Action cancelled")
        if isinstance(outcome, ToolFailed):
            return ActionResult.failed(
                action_id=action_id,
                summary=f"{entry.name} failed",
                error_category=outcome.error.code.value,
            )
        return ActionResult.failed(action_id=action_id, summary=f"{entry.name} failed", error_category="INVALID_OUTCOME")

    def _audit(self, result: ActionResult) -> None:
        if self.hooks.audit is not None:
            self.hooks.audit(result.to_event())

    def stream(self, request, sink):
        entry = self._entry(request.tool_ref)
        self._preflight(request, entry)
        outcome = self.runtime.stream(request, sink)
        result = self._project(request.invocation_id, entry, outcome)
        self._audit(result)
        return result

    def request_cancel(self, invocation_id, context=None, reason: str = "cancel requested") -> bool:
        return self.runtime.request_cancel(invocation_id, context, reason)

    def inspect(self, invocation_id, context=None):
        return self.runtime.inspect(invocation_id, context)


ToolRuntimeComposition = ProductionToolRuntime


def compose_tool_runtime(**kwargs: object) -> ProductionToolRuntime:
    return ProductionToolRuntime(**kwargs)


__all__ = ["ProductionToolRuntime", "RuntimeHooks", "ToolRuntimeComposition", "compose_tool_runtime"]
