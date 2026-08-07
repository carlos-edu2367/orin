from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from typing import Callable

from agentos.events.models import DataClassification, EventEnvelope

from .models import (
    Atomicity,
    FilesystemEntry,
    FilesystemError,
    FilesystemErrorCode,
    FilesystemLimits,
    FilesystemMutationResult,
    FilesystemOperationContext,
    FilesystemReadResult,
    FilesystemWriteResult,
    SymlinkPolicy,
    WorkspacePath,
)
from .ports import ByteSink, ByteSource, WorkspaceRootResolver
from .security import reject_empty_destructive_path


class InMemoryFilesystemEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def append(self, event: EventEnvelope) -> None:
        self.events.append(event)


class FilesystemService:
    def __init__(self, adapter, root_resolver: WorkspaceRootResolver, *, handle_validator: Callable[..., bool] | None = None, quota=None, event_sink=None, clock=None) -> None:
        self.adapter = adapter
        self.root_resolver = root_resolver
        self.handle_validator = handle_validator or (lambda handle, **_: False)
        self.quota = quota
        self.event_sink = event_sink or InMemoryFilesystemEventSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sequences: dict[str, int] = {}
        self._idempotency: dict[tuple[tuple[str, ...], str], tuple[tuple[object, ...], object]] = {}
        self._closed_leases: set[str] = set()

    def cleanup_lease(self, lease_id: str) -> bool:
        self._closed_leases.add(lease_id)
        return True

    def _root(self, context: FilesystemOperationContext):
        root = self.root_resolver.resolve(context)
        if isinstance(root, FilesystemError):
            return root
        if root.workspace_id != context.workspace_id or not self.root_resolver.revalidate(root, context):
            return FilesystemError(FilesystemErrorCode.UNSAFE_ROOT, "root validation failed")
        return root

    def _authorize(self, operation_id, context, lease_id, resource_handle):
        try:
            allowed = self.handle_validator(resource_handle, lease_id=lease_id, context=context, operation_id=operation_id)
        except Exception:
            allowed = False
        if not allowed:
            return FilesystemError(FilesystemErrorCode.INVALID_HANDLE, "resource authorization failed")
        return self._root(context)

    @staticmethod
    def _path_hash(path: WorkspacePath) -> str:
        return sha256(path.as_logical_string().encode()).hexdigest()[:16]

    def _event(self, event_type: str, context: FilesystemOperationContext, operation_id: str, path: WorkspacePath, *, outcome: str, version: int | None = None, reason: str | None = None) -> None:
        sequence = self._sequences.get(context.execution_id, 0) + 1
        self._sequences[context.execution_id] = sequence
        payload = {"operation_id": operation_id, "path_hash": self._path_hash(path), "outcome": outcome}
        if version is not None:
            payload["version"] = version
        if reason is not None:
            payload["reason_code"] = reason[:64]
        self.event_sink.append(EventEnvelope(event_id=f"filesystem-event:{context.execution_id}:{sequence}", event_type=event_type, event_version=1, occurred_at=self._clock(), source="filesystem", correlation_id=context.correlation_id, causation_id=operation_id, sequence=sequence, user_id=context.user_id, workspace_id=context.workspace_id, agent_id=context.agent_id, execution_id=context.execution_id, classification=DataClassification.INTERNAL, payload=payload))

    def _reject(self, result, context, operation_id, path):
        if isinstance(result, FilesystemError):
            self._event("FilesystemOperationRejected", context, operation_id, path, outcome=result.code.value, reason=result.code.value)
        return result

    def stat(self, *, operation_id, context, lease_id, resource_handle, path, limits, symlink_policy=SymlinkPolicy.REJECT, expected_version=None):
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, path)
        result = self.adapter.stat(root, path, expected_version=expected_version)
        return self._reject(result, context, operation_id, path) if isinstance(result, FilesystemError) else result

    def list(self, *, operation_id, context, lease_id, resource_handle, path, limits, recursive=False):
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, path)
        result = self.adapter.list(root, path, recursive=recursive, maximum_entries=min(limits.maximum_entries, 10000))
        return self._reject(result, context, operation_id, path) if isinstance(result, FilesystemError) else result

    def read(self, *, operation_id, context, lease_id, resource_handle, path, sink: ByteSink, offset_bytes, limits: FilesystemLimits, expected_version=None):
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, path)
        if offset_bytes < 0:
            return self._reject(FilesystemError(FilesystemErrorCode.INVALID_REQUEST), context, operation_id, path)
        result = self.adapter.read(root, path, sink, offset_bytes=offset_bytes, maximum_bytes=limits.maximum_bytes, expected_version=expected_version)
        if isinstance(result, FilesystemError):
            return self._reject(result, context, operation_id, path)
        self._event("FilesystemReadFinished", context, operation_id, path, outcome="APPLIED")
        return result

    def create_directory(self, *, operation_id, context, lease_id, resource_handle, path, limits, create_parents=False):
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, path)
        reservation = None
        if self.quota is not None:
            reservation = self.quota.reserve(context, lease_id, 0, 1, len(path.segments), limits.maximum_bytes, operation_id, operation_id)
            if hasattr(reservation, "code"):
                return self._reject(FilesystemError(FilesystemErrorCode.QUOTA_EXCEEDED), context, operation_id, path)
        result = self.adapter.create_directory(root, path, create_parents=create_parents, maximum_depth=limits.maximum_depth)
        if isinstance(result, FilesystemError):
            if reservation is not None:
                self.quota.release(reservation, context, lease_id, operation_id)
            return self._reject(result, context, operation_id, path)
        if reservation is not None:
            recorded = self.quota.record(reservation, context, lease_id, 0, 1, operation_id)
            if hasattr(recorded, "code"):
                return self._reject(FilesystemError(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN"), context, operation_id, path)
        self._event("FilesystemEntryCreated", context, operation_id, path, outcome="APPLIED", version=result.version)
        return result

    def write(self, *, operation_id, context, lease_id, resource_handle, path, source: ByteSource, mode, atomicity, limits: FilesystemLimits, expected_version=None, idempotency_key=""):
        key = (context.scope_key(), idempotency_key) if idempotency_key else None
        fingerprint = (path.segments, str(mode), str(atomicity), expected_version, limits.maximum_bytes, limits.maximum_entries)
        if key is not None and key in self._idempotency:
            previous_fingerprint, previous_result = self._idempotency[key]
            return previous_result if previous_fingerprint == fingerprint else FilesystemError(FilesystemErrorCode.CONFLICT, "idempotency binding conflict")
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, path)
        payload = source.read(limits.maximum_bytes + 1)
        if len(payload) > limits.maximum_bytes:
            return self._reject(FilesystemError(FilesystemErrorCode.QUOTA_EXCEEDED), context, operation_id, path)
        reservation = None
        if self.quota is not None:
            reservation = self.quota.reserve(context, lease_id, len(payload), 1, len(path.segments), limits.maximum_bytes, operation_id, idempotency_key or operation_id)
            if isinstance(reservation, FilesystemError) or hasattr(reservation, "code"):
                return self._reject(reservation, context, operation_id, path)
        result = self.adapter.write(root, path, BytesIO(payload), mode=str(mode), atomicity=str(atomicity), maximum_bytes=limits.maximum_bytes, maximum_entries=limits.maximum_entries, expected_version=expected_version)
        if isinstance(result, FilesystemError):
            if reservation is not None:
                self.quota.release(reservation, context, lease_id, operation_id)
            return self._reject(result, context, operation_id, path)
        if reservation is not None:
            recorded = self.quota.record(reservation, context, lease_id, result.bytes_written, 1, operation_id)
            if isinstance(recorded, FilesystemError) or hasattr(recorded, "code"):
                return self._reject(FilesystemError(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN"), context, operation_id, path)
        if key is not None:
            self._idempotency[key] = (fingerprint, result)
        self._event("FilesystemEntryCreated" if result.entry.version == 1 else "FilesystemEntryChanged", context, operation_id, path, outcome="APPLIED", version=result.entry.version)
        return result

    def copy(self, *, operation_id, context, lease_id, resource_handle, source, destination, limits, expected_source_version, overwrite="NEVER", idempotency_key=""):
        return self._copy_or_move(False, operation_id, context, lease_id, resource_handle, source, destination, limits, expected_source_version, overwrite, idempotency_key)

    def move(self, *, operation_id, context, lease_id, resource_handle, source, destination, limits, expected_source_version, overwrite="NEVER", idempotency_key=""):
        return self._copy_or_move(True, operation_id, context, lease_id, resource_handle, source, destination, limits, expected_source_version, overwrite, idempotency_key)

    def _copy_or_move(self, moving, operation_id, context, lease_id, resource_handle, source, destination, limits, expected_source_version, overwrite, idempotency_key):
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, source)
        if not self.root_resolver.revalidate(root, context):
            return self._reject(FilesystemError(FilesystemErrorCode.UNSAFE_ROOT), context, operation_id, source)
        reservation = None
        source_entry = None
        if not moving and self.quota is not None:
            source_entry = self.adapter.stat(root, source, expected_version=expected_source_version)
            if isinstance(source_entry, FilesystemError):
                return self._reject(source_entry, context, operation_id, source)
            reservation = self.quota.reserve(context, lease_id, source_entry.size_bytes, 1, len(destination.segments), limits.maximum_bytes, operation_id, idempotency_key or operation_id)
            if hasattr(reservation, "code"):
                return self._reject(FilesystemError(FilesystemErrorCode.QUOTA_EXCEEDED), context, operation_id, source)
        method = self.adapter.move if moving else self.adapter.copy
        kwargs = {"expected_source_version": expected_source_version, "overwrite": str(overwrite)}
        if not moving:
            kwargs.update(maximum_bytes=limits.maximum_bytes, maximum_entries=limits.maximum_entries)
        result = method(root, source, destination, **kwargs)
        if isinstance(result, FilesystemError):
            if reservation is not None:
                self.quota.release(reservation, context, lease_id, operation_id)
            return self._reject(result, context, operation_id, source)
        if reservation is not None:
            recorded = self.quota.record(reservation, context, lease_id, result.entry.size_bytes if result.entry else 0, 1, operation_id)
            if hasattr(recorded, "code"):
                return self._reject(FilesystemError(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN"), context, operation_id, destination)
        self._event("FilesystemEntryChanged", context, operation_id, destination, outcome="APPLIED", version=result.entry.version if result.entry else None)
        return result

    def remove(self, *, operation_id, context, lease_id, resource_handle, path, limits, expected_version=None, recursive=False, idempotency_key=""):
        try:
            reject_empty_destructive_path(path)
        except ValueError:
            return self._reject(FilesystemError(FilesystemErrorCode.REJECTED), context, operation_id, path)
        root = self._authorize(operation_id, context, lease_id, resource_handle)
        if isinstance(root, FilesystemError):
            return self._reject(root, context, operation_id, path)
        result = self.adapter.remove(root, path, expected_version=expected_version, recursive=recursive, maximum_entries=limits.maximum_entries)
        if isinstance(result, FilesystemError):
            return self._reject(result, context, operation_id, path)
        self._event("FilesystemEntryRemoved", context, operation_id, path, outcome="APPLIED")
        return result


__all__ = ["FilesystemService", "InMemoryFilesystemEventSink"]
