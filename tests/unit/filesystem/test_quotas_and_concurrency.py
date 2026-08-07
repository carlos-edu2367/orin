from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from agentos.filesystem.in_memory import InMemoryFilesystemAdapter, InMemoryWorkspaceRootResolver
from agentos.filesystem.models import Atomicity, FilesystemError, FilesystemErrorCode, FilesystemLimits, FilesystemOperationContext, OpaqueFilesystemHandle, WorkspacePath, WriteMode
from agentos.filesystem.service import FilesystemService


def test_concurrent_create_new_never_overwrites_or_creates_two_versions() -> None:
    ctx = FilesystemOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    resolver = InMemoryWorkspaceRootResolver(); resolver.provision(ctx)
    fs = FilesystemService(InMemoryFilesystemAdapter(), resolver, handle_validator=lambda handle, **_: True)
    kwargs = dict(context=ctx, lease_id="lease", resource_handle=OpaqueFilesystemHandle("h", "lease"), path=WorkspacePath.from_string("same"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(10, 1, 1))
    def write(index: int):
        return fs.write(operation_id=f"op-{index}", source=BytesIO(bytes([index])), idempotency_key=f"unique-{index}", **kwargs)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))
    assert sum(not isinstance(result, FilesystemError) for result in results) == 1


def test_limits_reject_oversized_write_before_effect() -> None:
    ctx = FilesystemOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    resolver = InMemoryWorkspaceRootResolver(); resolver.provision(ctx)
    fs = FilesystemService(InMemoryFilesystemAdapter(), resolver, handle_validator=lambda handle, **_: True)
    result = fs.write(operation_id="large", context=ctx, lease_id="lease", resource_handle=OpaqueFilesystemHandle("h", "lease"), path=WorkspacePath.from_string("large"), source=BytesIO(b"12345"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(4, 2, 2), idempotency_key="large")
    assert isinstance(result, FilesystemError) and result.code is FilesystemErrorCode.QUOTA_EXCEEDED


def test_create_directory_and_copy_use_quota_reservations_before_effect() -> None:
    class Quota:
        def __init__(self):
            self.reserved = []
            self.recorded = []
        def reserve(self, context, lease_id, bytes_count, entries_count, depth, maximum_file_bytes, operation_id, idempotency_key):
            token = (bytes_count, entries_count, depth)
            self.reserved.append(token)
            return token
        def record(self, reservation, context, lease_id, bytes_effective, entries_effective, operation_id):
            self.recorded.append((bytes_effective, entries_effective))
            return object()
        def release(self, reservation, context, lease_id, operation_id):
            return object()

    ctx = FilesystemOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    resolver = InMemoryWorkspaceRootResolver(); resolver.provision(ctx)
    quota = Quota()
    fs = FilesystemService(InMemoryFilesystemAdapter(), resolver, handle_validator=lambda handle, **_: True, quota=quota)
    fs.create_directory(operation_id="mkdir", context=ctx, lease_id="lease", resource_handle=OpaqueFilesystemHandle("h", "lease"), path=WorkspacePath.from_string("dir"), limits=FilesystemLimits(10, 5, 2))
    fs.write(operation_id="write", context=ctx, lease_id="lease", resource_handle=OpaqueFilesystemHandle("h", "lease"), path=WorkspacePath.from_string("dir/file"), source=BytesIO(b"123"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(10, 5, 2), idempotency_key="write")
    fs.copy(operation_id="copy", context=ctx, lease_id="lease", resource_handle=OpaqueFilesystemHandle("h", "lease"), source=WorkspacePath.from_string("dir/file"), destination=WorkspacePath.from_string("dir/copy"), limits=FilesystemLimits(10, 5, 2), expected_source_version=1, idempotency_key="copy")
    assert quota.reserved[0] == (0, 1, 1)
    assert quota.reserved[-1] == (3, 1, 2)
