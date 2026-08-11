from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import Protocol

from .models import DisableTool, RegisterTool, RegistrationResult, ToolDescriptor, ToolRef, ToolRegistryOperationContext, ToolStatus


class ToolRegistry(Protocol):
    def resolve(self, tool_ref: ToolRef, context) -> ToolDescriptor: ...
    def tool(self, tool_ref: ToolRef): ...
    def list_authorized(self, context, *, statuses=(ToolStatus.ACTIVE, ToolStatus.DEPRECATED), permission_filter=()): ...


class InMemoryToolRegistry:
    """Version-exact append-only registry; intended as a test/reference adapter."""
    def __init__(self, *, bootstrap_allowlist: set[str] | None = None, authorization=None) -> None:
        self._entries: dict[ToolRef, tuple[ToolDescriptor, object, str]] = {}
        self._bootstrap_allowlist = bootstrap_allowlist
        self._authorization = authorization or (lambda context, action, descriptor: True)
        self.audit: list[tuple[str, ToolRef, str]] = []
        self._lock = RLock()

    def register_bootstrap(self, descriptor: ToolDescriptor, tool: object, *, integrity: str, responsible_user_id: str | None = None) -> ToolDescriptor:
        if self._bootstrap_allowlist is not None and integrity not in self._bootstrap_allowlist:
            raise PermissionError("bootstrap package integrity is not allowlisted")
        with self._lock:
            if descriptor.tool_ref in self._entries:
                raise ValueError("published tool version is immutable")
            self._entries[descriptor.tool_ref] = (descriptor, tool, integrity)
            self.audit.append(("BOOTSTRAP_REGISTERED", descriptor.tool_ref, responsible_user_id or "system"))
            return descriptor

    def register(self, descriptor: ToolDescriptor | RegisterTool, tool: object | None = None, *, context: ToolRegistryOperationContext | None = None, integrity: str | None = None) -> ToolDescriptor | RegistrationResult:
        if isinstance(descriptor, RegisterTool):
            request = descriptor
            result = self.register(request.descriptor, request.factory, context=request.context, integrity=request.package_integrity_ref)
            return RegistrationResult(result, "ACTIVATED")
        assert context is not None and integrity is not None and tool is not None
        if not self._authorization(context, "register", descriptor): raise PermissionError("registry registration is unauthorized")
        return self.register_bootstrap(descriptor, tool, integrity=integrity, responsible_user_id=context.user_id)

    def resolve(self, tool_ref: ToolRef, context) -> ToolDescriptor:
        with self._lock:
            if isinstance(tool_ref, str):
                matches = [item for item in self._entries.items() if item[0].tool_id == tool_ref or item[1][0].name == tool_ref]
                if len(matches) != 1:
                    raise LookupError("tool name is not registered")
                tool_ref = matches[0][0]
            entry = self._entries.get(tool_ref)
            if entry is None: raise LookupError("tool version is not registered")
            descriptor = entry[0]
            if descriptor.status is ToolStatus.DISABLED: raise PermissionError("tool version is disabled")
            if not self._authorization(context, "invoke", descriptor):
                raise PermissionError("tool invocation is unauthorized")
            return descriptor

    def tool(self, tool_ref: ToolRef) -> object:
        with self._lock:
            try: return self._entries[tool_ref][1]
            except KeyError as exc: raise LookupError("tool version is not registered") from exc

    def disable(self, tool_ref: ToolRef | DisableTool, *, context: ToolRegistryOperationContext | None = None, reason: str | None = None) -> ToolDescriptor | RegistrationResult:
        requested = isinstance(tool_ref, DisableTool)
        if requested:
            request = tool_ref
            tool_ref, context, reason = request.tool_ref, request.context, request.reason
        assert context is not None and reason is not None
        if not self._authorization(context, "disable", tool_ref): raise PermissionError("registry disable is unauthorized")
        with self._lock:
            descriptor, tool, integrity = self._entries[tool_ref]
            if descriptor.status is ToolStatus.DISABLED: return RegistrationResult(descriptor, "DISABLED") if requested else descriptor
            updated = replace(descriptor, status=ToolStatus.DISABLED)
            self._entries[tool_ref] = (updated, tool, integrity)
            self.audit.append(("DISABLED", tool_ref, reason[:128]))
            return RegistrationResult(updated, "DISABLED") if requested else updated

    def list(self, context, *, statuses: tuple[ToolStatus, ...] = (ToolStatus.ACTIVE, ToolStatus.DEPRECATED)) -> tuple[ToolDescriptor, ...]:
        return self.list_authorized(context, statuses=statuses)

    def list_authorized(self, context, *, statuses: tuple[ToolStatus, ...] = (ToolStatus.ACTIVE, ToolStatus.DEPRECATED), permission_filter: tuple[str, ...] = ()) -> tuple[ToolDescriptor, ...]:
        required = set(permission_filter)
        with self._lock:
            visible = []
            for descriptor, _tool, _integrity in self._entries.values():
                if descriptor.status not in statuses:
                    continue
                if required and not required.issubset(set(descriptor.permissions)):
                    continue
                if not self._authorization(context, "list", descriptor):
                    continue
                visible.append(descriptor)
            return tuple(visible)

__all__ = ["ToolRegistry", "InMemoryToolRegistry"]
