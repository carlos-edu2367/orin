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
