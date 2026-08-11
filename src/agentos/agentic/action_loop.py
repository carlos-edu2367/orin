"""Safe projection and execution boundary for provider-requested actions."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from agentos.tool_runtime.models import SensitiveOperationContext, ToolInvocationRequest


class MalformedToolCall(ValueError):
    pass


def project_tool_schema(descriptor: object) -> dict[str, object]:
    """Expose only provider-safe tool metadata, never internal handles/policy."""
    return {"type": "function", "function": {"name": str(descriptor.name), "description": str(descriptor.description)[:512], "parameters": dict(descriptor.input_schema)}}


def _matches(value: object, schema: Mapping[str, object]) -> bool:
    kind = schema.get("type")
    if kind == "object":
        return isinstance(value, Mapping) and set(value) <= set((schema.get("properties") or {}).keys()) and set(schema.get("required") or ()) <= set(value) and all(_matches(value[key], child) for key, child in (schema.get("properties") or {}).items() if key in value)
    if kind == "array":
        return isinstance(value, list) and all(_matches(item, schema.get("items") or {}) for item in value)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return False


@dataclass(frozen=True, slots=True)
class ActionBatch:
    results: tuple[dict[str, object], ...]
    count: int


class ActionLoop:
    def __init__(self, registry: object | None, tool_runtime: object | None, *, max_argument_bytes: int = 32_768) -> None:
        self.registry, self.tool_runtime, self.max_argument_bytes = registry, tool_runtime, max_argument_bytes
        self._seen_call_ids: set[str] = set()

    def tool_schemas(self, context: object) -> list[dict[str, object]]:
        if self.registry is None or not hasattr(self.registry, "list"):
            return []
        return [project_tool_schema(descriptor) for descriptor in self.registry.list(context)]

    def execute(self, calls: list[object], context: object, *, action_count: int, max_actions: int) -> ActionBatch:
        if action_count + len(calls) > max_actions:
            raise MalformedToolCall("maximum action count exceeded")
        prepared: list[tuple[object, str, str, Mapping[str, object], object]] = []
        pending_ids: set[str] = set()
        for call in calls:
            call_id = str(call.get("id") if isinstance(call, Mapping) else getattr(call, "tool_call_id", ""))
            name = str(call.get("name") if isinstance(call, Mapping) else getattr(call, "tool_name", ""))
            raw_args = call.get("arguments") if isinstance(call, Mapping) else getattr(call, "arguments", "")
            if not call_id or not name or call_id in self._seen_call_ids or call_id in pending_ids:
                raise MalformedToolCall("tool call identity is invalid or duplicated")
            if isinstance(raw_args, str):
                if len(raw_args.encode("utf-8")) > self.max_argument_bytes:
                    raise MalformedToolCall("tool arguments exceed the bounded input")
                try:
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError as error:
                    raise MalformedToolCall("tool arguments are not valid JSON") from error
            else:
                arguments = raw_args
            if not isinstance(arguments, Mapping):
                raise MalformedToolCall("tool arguments must be an object")
            if self.registry is None or not hasattr(self.registry, "resolve"):
                raise MalformedToolCall("tool is not registered")
            try:
                descriptor = self.registry.resolve(name, context)
            except (LookupError, PermissionError) as error:
                raise MalformedToolCall("tool is not registered or authorized") from error
            if not _matches(arguments, descriptor.input_schema):
                raise MalformedToolCall("tool arguments do not satisfy the closed schema")
            pending_ids.add(call_id)
            prepared.append((call, call_id, name, arguments, descriptor))
        results: list[dict[str, object]] = []
        for call, call_id, name, arguments, descriptor in prepared:
            self._seen_call_ids.add(call_id)
            invocation_id = f"agentic-action:{call_id}"
            if isinstance(context, SensitiveOperationContext):
                operation_context = context
            else:
                operation_context = SensitiveOperationContext(str(getattr(context, "user_id", "user")), getattr(context, "workspace_id", None), str(getattr(context, "agent_id", "agent")), str(getattr(context, "execution_id", "execution")), str(getattr(context, "correlation_id", "correlation")), "agentic.action", "agentic-runtime")
            request = ToolInvocationRequest(invocation_id, descriptor.tool_ref, operation_context, dict(arguments), idempotency_key=invocation_id)
            if self.tool_runtime is None:
                raise MalformedToolCall("tool runtime is not configured")
            outcome = self.tool_runtime.invoke(request) if hasattr(self.tool_runtime, "invoke") else self.tool_runtime(request)
            results.append(self._public_result(call_id, name, outcome))
        return ActionBatch(tuple(results), action_count + len(results))

    @staticmethod
    def _public_result(call_id: str, name: str, outcome: object) -> dict[str, object]:
        class_name = type(outcome).__name__
        status_value = getattr(getattr(outcome, "status", None), "value", getattr(outcome, "status", None))
        if class_name == "ToolSucceeded" or status_value == "SUCCEEDED" or getattr(outcome, "result_ref", None) is not None and not hasattr(outcome, "error"):
            status = "succeeded"
        elif class_name == "ToolCancelled" or status_value == "CANCELLED":
            status = "cancelled"
        else:
            status = "failed"
        error = getattr(outcome, "error", None)
        return {"id": call_id, "name": name, "status": status, "result_ref": getattr(outcome, "result_ref", None), "error_code": str(getattr(error, "code", "ACTION_FAILED")) if error else None, "summary": f"{name} {status}"}


__all__ = ["ActionBatch", "ActionLoop", "MalformedToolCall", "project_tool_schema"]
