from __future__ import annotations

from io import BytesIO
from pathlib import Path

from agentos.filesystem.in_memory import InMemoryFilesystemAdapter, InMemoryWorkspaceRootResolver
from agentos.filesystem.models import (
    Atomicity,
    FilesystemEntryKind,
    FilesystemError,
    FilesystemErrorCode,
    FilesystemLimits,
    FilesystemOperationContext,
    OpaqueFilesystemHandle,
    OverwritePolicy,
    SymlinkPolicy,
    WorkspacePath,
    WriteMode,
)
from agentos.filesystem.service import FilesystemService


def context(*, purpose: str = "filesystem.write", workspace_id: str = "ws-1") -> FilesystemOperationContext:
    return FilesystemOperationContext("user-1", workspace_id, "agent-1", "exec-1", "corr-1", purpose, "agent:agent-1")


def service() -> FilesystemService:
    resolver = InMemoryWorkspaceRootResolver()
    resolver.provision(context())
    return FilesystemService(
        InMemoryFilesystemAdapter(),
        resolver,
        handle_validator=lambda handle, **kwargs: isinstance(handle, OpaqueFilesystemHandle)
        and handle.binding == f"lease:{kwargs['lease_id']}",
    )


def handle() -> OpaqueFilesystemHandle:
    return OpaqueFilesystemHandle("h-1", "lease:lease-1")


def limits(**overrides) -> FilesystemLimits:
    return FilesystemLimits(maximum_bytes=100, maximum_entries=20, maximum_depth=5, **overrides)


def test_in_memory_filesystem_supports_directory_write_stat_list_read_copy_move_and_remove() -> None:
    fs = service()
    h = handle()
    assert not isinstance(fs.create_directory(operation_id="mkdir", context=context(), lease_id="lease-1", resource_handle=h, path=WorkspacePath.from_string("docs"), limits=limits()), FilesystemError)
    written = fs.write(operation_id="write", context=context(), lease_id="lease-1", resource_handle=h, path=WorkspacePath.from_string("docs/report.txt"), source=BytesIO(b"hello"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="write-1")
    assert written.entry.kind is FilesystemEntryKind.FILE
    assert written.bytes_written == 5
    listed = fs.list(operation_id="list", context=context(purpose="filesystem.list"), lease_id="lease-1", resource_handle=h, path=WorkspacePath.root(), limits=limits())
    assert [entry.path.as_logical_string() for entry in listed.entries] == ["docs"]
    sink = BytesIO()
    read = fs.read(operation_id="read", context=context(purpose="filesystem.read"), lease_id="lease-1", resource_handle=h, path=WorkspacePath.from_string("docs/report.txt"), sink=sink, offset_bytes=0, limits=limits())
    assert read.bytes_read == 5 and sink.getvalue() == b"hello"
    copied = fs.copy(operation_id="copy", context=context(), lease_id="lease-1", resource_handle=h, source=WorkspacePath.from_string("docs/report.txt"), destination=WorkspacePath.from_string("docs/copy.txt"), limits=limits(), expected_source_version=written.entry.version, overwrite=OverwritePolicy.NEVER, idempotency_key="copy-1")
    assert copied.affected_entries == 1
    moved = fs.move(operation_id="move", context=context(), lease_id="lease-1", resource_handle=h, source=WorkspacePath.from_string("docs/copy.txt"), destination=WorkspacePath.from_string("moved.txt"), limits=limits(), expected_source_version=copied.entry.version if copied.entry else 1, overwrite=OverwritePolicy.NEVER, idempotency_key="move-1")
    assert moved.entry is not None and moved.entry.path.as_logical_string() == "moved.txt"
    removed = fs.remove(operation_id="remove", context=context(), lease_id="lease-1", resource_handle=h, path=WorkspacePath.from_string("moved.txt"), limits=limits(), expected_version=moved.entry.version, idempotency_key="remove-1")
    assert removed.affected_entries == 1


def test_filesystem_rejects_invalid_handle_cross_workspace_and_version_conflict_before_effect() -> None:
    fs = service()
    bad = OpaqueFilesystemHandle("h-2", "lease:other")
    rejected = fs.write(operation_id="bad", context=context(), lease_id="lease-1", resource_handle=bad, path=WorkspacePath.from_string("a.txt"), source=BytesIO(b"x"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="bad")
    assert isinstance(rejected, FilesystemError) and rejected.code is FilesystemErrorCode.INVALID_HANDLE
    created = fs.write(operation_id="create", context=context(), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("a.txt"), source=BytesIO(b"x"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="create")
    conflict = fs.write(operation_id="conflict", context=context(), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("a.txt"), source=BytesIO(b"y"), mode=WriteMode.REPLACE, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), expected_version=99, idempotency_key="conflict")
    assert created.entry.version == 1
    assert isinstance(conflict, FilesystemError) and conflict.code is FilesystemErrorCode.CONFLICT
    cross = fs.stat(operation_id="cross", context=context(workspace_id="ws-2"), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("a.txt"), limits=limits())
    assert isinstance(cross, FilesystemError) and cross.code is FilesystemErrorCode.NOT_FOUND


def test_idempotent_write_returns_same_confirmed_result_without_duplicate_entry() -> None:
    fs = service()
    first = fs.write(operation_id="write", context=context(), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("same.txt"), source=BytesIO(b"same"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="same")
    second = fs.write(operation_id="retry", context=context(), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("same.txt"), source=BytesIO(b"same"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="same")
    assert second == first


def test_reusing_idempotency_key_for_a_different_target_is_a_conflict() -> None:
    fs = service()
    fs.write(operation_id="write", context=context(), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("one.txt"), source=BytesIO(b"one"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="same-key")
    conflict = fs.write(operation_id="retry", context=context(), lease_id="lease-1", resource_handle=handle(), path=WorkspacePath.from_string("two.txt"), source=BytesIO(b"two"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=limits(), idempotency_key="same-key")
    assert isinstance(conflict, FilesystemError) and conflict.code is FilesystemErrorCode.CONFLICT
