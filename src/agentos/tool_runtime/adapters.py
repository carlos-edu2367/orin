"""Atomic tool adapters for the already-authorized domain services.

They deliberately translate only structured inputs to one operation each. Lease
handles are supplied by ToolRuntime; no adapter receives a registry or runtime.
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO
import re
from typing import Any, Mapping

from .models import AtomicToolCall


def sanitize_adapter_result(result: object) -> dict[str, Any]:
    """Keep only bounded public summary/artifact fields from an adapter result.

    Adapter output may contain bytes, logical paths, command output, handles,
    credentials, or prompts. None of those fields are allowed to cross into a
    provider or event projection.
    """
    if not isinstance(result, Mapping):
        return {}
    public: dict[str, Any] = {}
    summary = result.get("summary")
    if isinstance(summary, str) and summary.strip() and len(summary) <= 256:
        public["summary"] = summary
    values: list[object] = []
    if result.get("artifact_ref") is not None:
        values.append(result["artifact_ref"])
    refs = result.get("artifact_refs", ())
    if isinstance(refs, (tuple, list)):
        values.extend(refs)
    artifact_refs: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            continue
        if any(marker in value for marker in ("/", "\\", "\n", "\r")):
            continue
        if value not in artifact_refs:
            artifact_refs.append(value)
    if artifact_refs:
        public["artifact_refs"] = artifact_refs[:16]
    return public


_SENSITIVE_KEY = re.compile(r"(?:api|access|secret|auth|token|password|credential|private|provider|prompt|header|cookie|tool).*?(?:key|value|output|input|token|secret)?", re.I)
_SENSITIVE_FRAGMENT = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|credential|authorization|provider[_-]?output|tool[_-]?args)\s*[:=]\s*[^\s,;]+")


def sanitize_stream_value(value: object, *, depth: int = 0) -> object:
    """Return a small, JSON-safe progress payload with secret-like data removed."""
    if depth > 4:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        public: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64 or _SENSITIVE_KEY.search(key.replace("_", "")):
                continue
            sanitized = sanitize_stream_value(item, depth=depth + 1)
            if sanitized is not None:
                public[key] = sanitized
        return public
    if isinstance(value, (list, tuple)):
        return [sanitize_stream_value(item, depth=depth + 1) for item in value[:32]]
    if isinstance(value, str):
        bounded = value[:256]
        return _SENSITIVE_FRAGMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", bounded)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSUPPORTED]"


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


__all__ = [
    "ArtifactInspectAtomicTool",
    "BrowserNavigateAtomicTool",
    "FilesystemAtomicTool",
    "TerminalCommandAtomicTool",
    "sanitize_adapter_result",
    "sanitize_stream_value",
]
