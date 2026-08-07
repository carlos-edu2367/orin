from __future__ import annotations

from typing import Protocol

from agentos.workspaces.models import FilesystemObjectIdentity

from .models import (
    FilesystemEntry,
    FilesystemError,
    FilesystemLimits,
    FilesystemMutationResult,
    FilesystemOperationContext,
    FilesystemPage,
    FilesystemReadResult,
    FilesystemWriteResult,
    OpaqueFilesystemHandle,
    SymlinkPolicy,
    WorkspacePath,
)


class ByteSource(Protocol):
    def read(self, maximum_bytes: int) -> bytes: ...


class ByteSink(Protocol):
    def write(self, data: bytes) -> int | None: ...


class WorkspaceRootResolver(Protocol):
    def resolve(self, context: FilesystemOperationContext) -> "CanonicalWorkspaceRoot | FilesystemError": ...

    def revalidate(self, root: "CanonicalWorkspaceRoot", context: FilesystemOperationContext) -> bool: ...


class CanonicalWorkspaceRoot:
    __slots__ = ("root_ref", "workspace_id", "identity", "policy_version")

    def __init__(self, root_ref: str, workspace_id: str, identity: FilesystemObjectIdentity, policy_version: int) -> None:
        if not root_ref or not workspace_id or policy_version < 1:
            raise ValueError("canonical root is invalid")
        self.root_ref = root_ref
        self.workspace_id = workspace_id
        self.identity = identity
        self.policy_version = policy_version

    def __repr__(self) -> str:
        return "CanonicalWorkspaceRoot(root_ref=<opaque>, workspace_id=<opaque>, identity=<opaque>, policy_version=%d)" % self.policy_version


class ResourceHandleValidator(Protocol):
    def validate_filesystem_handle(self, handle: object, *, lease_id: str, context: FilesystemOperationContext, operation_id: str) -> bool: ...


class WorkspaceQuotaPort(Protocol):
    def reserve(self, context: FilesystemOperationContext, lease_id: str, bytes_count: int, entries_count: int, depth: int, maximum_file_bytes: int, operation_id: str, idempotency_key: str) -> object | FilesystemError: ...
    def record(self, reservation: object, context: FilesystemOperationContext, lease_id: str, bytes_effective: int, entries_effective: int, operation_id: str) -> object | FilesystemError: ...
    def release(self, reservation: object, context: FilesystemOperationContext, lease_id: str, operation_id: str) -> object | FilesystemError: ...


class FilesystemPort(Protocol):
    def stat(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, path: WorkspacePath, limits: FilesystemLimits, symlink_policy: SymlinkPolicy = SymlinkPolicy.REJECT, expected_version: int | None = None) -> FilesystemEntry | FilesystemError: ...
    def list(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, path: WorkspacePath, limits: FilesystemLimits, recursive: bool = False) -> FilesystemPage | FilesystemError: ...
    def read(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, path: WorkspacePath, sink: ByteSink, offset_bytes: int, limits: FilesystemLimits, expected_version: int | None = None) -> FilesystemReadResult | FilesystemError: ...
    def create_directory(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, path: WorkspacePath, limits: FilesystemLimits, create_parents: bool = False) -> FilesystemEntry | FilesystemError: ...
    def write(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, path: WorkspacePath, source: ByteSource, mode: str, atomicity: str, limits: FilesystemLimits, expected_version: int | None = None, idempotency_key: str = "") -> FilesystemWriteResult | FilesystemError: ...
    def move(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, source: WorkspacePath, destination: WorkspacePath, limits: FilesystemLimits, expected_source_version: int, overwrite: str = "NEVER", idempotency_key: str = "") -> FilesystemMutationResult | FilesystemError: ...
    def copy(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, source: WorkspacePath, destination: WorkspacePath, limits: FilesystemLimits, expected_source_version: int, overwrite: str = "NEVER", idempotency_key: str = "") -> FilesystemMutationResult | FilesystemError: ...
    def remove(self, *, operation_id: str, context: FilesystemOperationContext, lease_id: str, resource_handle: object, path: WorkspacePath, limits: FilesystemLimits, expected_version: int | None = None, recursive: bool = False, idempotency_key: str = "") -> FilesystemMutationResult | FilesystemError: ...


__all__ = [name for name in globals() if not name.startswith("_")]
