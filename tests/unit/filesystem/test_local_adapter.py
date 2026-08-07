from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from agentos.filesystem.local import LocalFilesystemAdapter, LocalWorkspaceRootResolver
from agentos.filesystem.models import Atomicity, FilesystemError, FilesystemErrorCode, FilesystemLimits, FilesystemOperationContext, OpaqueFilesystemHandle, WorkspacePath, WriteMode
from agentos.filesystem.service import FilesystemService


def context() -> FilesystemOperationContext:
    return FilesystemOperationContext("u", "ws-1", "a", "e", "c", "filesystem.write", "agent:a")


def make_service(tmp_path: Path):
    resolver = LocalWorkspaceRootResolver(tmp_path)
    resolver.root_for(context())
    adapter = LocalFilesystemAdapter(resolver)
    return FilesystemService(adapter, resolver, handle_validator=lambda handle, **_: isinstance(handle, OpaqueFilesystemHandle) and handle.binding == "lease:1"), resolver, adapter


def test_local_adapter_uses_only_provisioned_root_and_supports_atomic_write(tmp_path: Path) -> None:
    fs, resolver, _ = make_service(tmp_path)
    result = fs.write(operation_id="write", context=context(), lease_id="1", resource_handle=OpaqueFilesystemHandle("h", "lease:1"), path=WorkspacePath.from_string("report.txt"), source=BytesIO(b"safe"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(100, 5, 2), idempotency_key="write")
    assert not isinstance(result, FilesystemError)
    assert result.entry.path.as_logical_string() == "report.txt"
    assert not (tmp_path / "report.txt").exists()
    root = resolver.root_for(context())
    assert root.root_ref


def test_local_adapter_rejects_symlink_escape_and_root_swap_before_effect(tmp_path: Path) -> None:
    fs, resolver, adapter = make_service(tmp_path)
    resolver.root_for(context())
    root_dir = resolver.location_for(context().workspace_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root_dir / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform cannot create symlinks for this test")
    rejected = fs.write(operation_id="link", context=context(), lease_id="1", resource_handle=OpaqueFilesystemHandle("h", "lease:1"), path=WorkspacePath.from_string("link/escape.txt"), source=BytesIO(b"no"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(100, 5, 2), idempotency_key="link")
    assert isinstance(rejected, FilesystemError) and rejected.code is FilesystemErrorCode.UNSAFE_ROOT
    adapter.before_effect = lambda: resolver.swap_identity("ws-1")
    swapped = fs.write(operation_id="swap", context=context(), lease_id="1", resource_handle=OpaqueFilesystemHandle("h", "lease:1"), path=WorkspacePath.from_string("safe.txt"), source=BytesIO(b"no"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(100, 5, 2), idempotency_key="swap")
    assert isinstance(swapped, FilesystemError) and swapped.code is FilesystemErrorCode.UNSAFE_ROOT
    assert not (root_dir / "safe.txt").exists()


def test_local_adapter_rejects_root_target_and_cross_boundary_copy(tmp_path: Path) -> None:
    fs, resolver, _ = make_service(tmp_path)
    handle = OpaqueFilesystemHandle("h", "lease:1")
    root_rejected = fs.remove(operation_id="root", context=context(), lease_id="1", resource_handle=handle, path=WorkspacePath.root(), limits=FilesystemLimits(100, 5, 2), recursive=True, idempotency_key="root")
    assert isinstance(root_rejected, FilesystemError) and root_rejected.code is FilesystemErrorCode.REJECTED
    created = fs.write(operation_id="write", context=context(), lease_id="1", resource_handle=handle, path=WorkspacePath.from_string("inside.txt"), source=BytesIO(b"safe"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(100, 5, 2), idempotency_key="inside")
    assert not isinstance(created, FilesystemError)
    outside = WorkspacePath.from_string("../outside") if False else WorkspacePath.from_segments("outside")
    copied = fs.copy(operation_id="copy", context=context(), lease_id="1", resource_handle=handle, source=WorkspacePath.from_string("inside.txt"), destination=outside, limits=FilesystemLimits(100, 5, 2), expected_source_version=created.entry.version, idempotency_key="copy")
    assert not isinstance(copied, FilesystemError)
    assert resolver.location_for("ws-1").joinpath("outside").exists()


def test_local_adapter_rejects_hard_link_ambiguity(tmp_path: Path) -> None:
    fs, resolver, _ = make_service(tmp_path)
    root_dir = resolver.location_for("ws-1")
    original = root_dir / "original.txt"
    original.write_text("content", encoding="utf-8")
    linked = root_dir / "linked.txt"
    try:
        linked.hardlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("platform cannot create hard links for this test")
    result = fs.stat(operation_id="hardlink", context=context(), lease_id="1", resource_handle=OpaqueFilesystemHandle("h", "lease:1"), path=WorkspacePath.from_string("linked.txt"), limits=FilesystemLimits(100, 5, 2))
    assert isinstance(result, FilesystemError) and result.code is FilesystemErrorCode.UNSAFE_ROOT
