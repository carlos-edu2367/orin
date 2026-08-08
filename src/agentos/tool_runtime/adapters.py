"""Atomic tool adapters for the already-authorized domain services.

They deliberately translate only structured inputs to one operation each. Lease
handles are supplied by ToolRuntime; no adapter receives a registry or runtime.
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from .models import AtomicToolCall


def _context(call: AtomicToolCall, module):
    return module(call.context.user_id, call.context.workspace_id, call.context.agent_id, call.context.execution_id, call.context.correlation_id, call.context.purpose, call.context.actor)


class FilesystemAtomicTool:
    def __init__(self, filesystem_service, operation: str) -> None:
        if operation not in {"stat", "list", "read", "create_directory", "write", "remove"}: raise ValueError("unsupported filesystem atomic operation")
        self._service, self._operation = filesystem_service, operation

    def execute(self, call: AtomicToolCall):
        from agentos.filesystem.models import FilesystemLimits, FilesystemOperationContext, WorkspacePath, WriteMode, Atomicity
        context = _context(call, FilesystemOperationContext)
        handle = call.resources[0]
        path = WorkspacePath.from_string(call.input["path"])
        common = dict(operation_id=call.invocation_id, context=context, lease_id=handle.lease_id, resource_handle=handle, path=path, limits=FilesystemLimits(timeout=max(timedelta(seconds=1), call.deadline - __import__('datetime').datetime.now(call.deadline.tzinfo))))
        if self._operation == "read":
            sink = BytesIO(); result = self._service.read(sink=sink, offset_bytes=0, **common)
            return {"bytes": sink.getvalue().decode("utf-8", "replace"), "bytes_read": getattr(result, "bytes_read", 0)}
        if self._operation == "write":
            data = call.input["data"].encode("utf-8")
            result = self._service.write(source=BytesIO(data), mode=WriteMode.CREATE, atomicity=Atomicity.REQUIRED, idempotency_key=call.invocation_id, **common)
            return {"bytes_written": getattr(result, "bytes_written", 0)}
        if self._operation == "list":
            result = self._service.list(recursive=False, **common)
            return {"entries": [item.path.as_logical_string() for item in getattr(result, "entries", ())]}
        if self._operation == "stat":
            result = self._service.stat(**common)
            return {"size_bytes": getattr(result, "size_bytes", 0), "version": getattr(result, "version", 0)}
        if self._operation == "create_directory":
            result = self._service.create_directory(create_parents=False, **common)
            return {"affected_entries": getattr(result, "affected_entries", 0)}
        result = self._service.remove(expected_version=call.input.get("expected_version"), recursive=False, idempotency_key=call.invocation_id, **common)
        return {"affected_entries": getattr(result, "affected_entries", 0)}


class TerminalCommandAtomicTool:
    def __init__(self, terminal_service) -> None: self._service = terminal_service

    def execute(self, call: AtomicToolCall):
        from agentos.terminal.models import ExecuteTerminalCommand, TerminalCommand, TerminalOperationContext
        from agentos.filesystem.models import WorkspacePath
        context = _context(call, TerminalOperationContext)
        handle = call.resources[0]
        command = TerminalCommand(call.invocation_id, call.input["session_id"], context, call.input["command"], WorkspacePath.from_string(call.input.get("cwd", "")) if call.input.get("cwd") else None, (), max(timedelta(seconds=1), call.deadline - __import__('datetime').datetime.now(call.deadline.tzinfo)), call.input.get("maximum_output_bytes", 65536), call.invocation_id)
        accepted = self._service.execute(ExecuteTerminalCommand(command))
        if hasattr(accepted, "code"): raise RuntimeError("terminal command rejected")
        return {"command_id": str(command.command_id), "accepted": True}


class BrowserNavigateAtomicTool:
    def __init__(self, browser_service) -> None: self._service = browser_service

    def execute(self, call: AtomicToolCall):
        from agentos.browser.models import BrowserOperationContext
        context = _context(call, BrowserOperationContext)
        result = self._service.navigate(context, call.input["page_id"], call.input["expected_page_version"], call.input["url"])
        if not result.applied: raise RuntimeError("browser navigation rejected")
        return {"page_id": result.snapshot.page_id, "url": result.snapshot.url, "version": result.snapshot.version}


class ArtifactInspectAtomicTool:
    def __init__(self, artifact_manager) -> None: self._manager = artifact_manager

    def execute(self, call: AtomicToolCall):
        # The opaque reference is intentionally accepted only as the storage
        # domain type, never resolved by this adapter from an arbitrary ID.
        from agentos.artifact_storage.models import ArtifactOperationContext
        from agentos.artifact_storage.ports import InspectArtifact
        context = _context(call, ArtifactOperationContext)
        result = self._manager.inspect(InspectArtifact(context, call.input["reference"], context.purpose))
        if hasattr(result, "code"): raise RuntimeError("artifact inspection rejected")
        return {"artifact_id": result.artifact_id, "size_bytes": result.size_bytes, "state": result.state.value}


__all__ = ["FilesystemAtomicTool", "TerminalCommandAtomicTool", "BrowserNavigateAtomicTool", "ArtifactInspectAtomicTool"]
