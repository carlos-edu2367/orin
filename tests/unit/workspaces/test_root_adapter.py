from __future__ import annotations

from agentos.workspaces.models import CreateWorkspaceContext, WorkspaceOperationContext, WorkspaceState
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter


def create_context() -> CreateWorkspaceContext:
    return CreateWorkspaceContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1")


def operation_context() -> WorkspaceOperationContext:
    return WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.write", "user:user-1")


def test_root_adapter_provisions_without_accepting_a_caller_root_and_binds_handle() -> None:
    adapter = InMemoryWorkspaceRootAdapter()
    descriptor = adapter.provision(create_context(), "ws-1")
    resolved = adapter.resolve(operation_context(), descriptor)
    assert resolved.handle is not None
    assert resolved.identity == descriptor.root_identity
    assert repr(resolved.handle) == "OpaqueRootHandleRef(<ephemeral>)"
    adapter.release(resolved.handle)
    released = adapter.resolve(operation_context(), descriptor)
    assert released.handle is not None


def test_root_adapter_fails_closed_for_links_mounts_reparse_points_and_identity_swap() -> None:
    adapter = InMemoryWorkspaceRootAdapter()
    descriptor = adapter.provision(create_context(), "ws-1")
    for issue in ("SYMLINK", "JUNCTION", "MOUNT", "REPARSE", "HARD_LINK_AMBIGUOUS", "EMPTY", "BROAD"):
        adapter.set_issue("ws-1", issue)
        resolved = adapter.resolve(operation_context(), descriptor)
        assert resolved.handle is None
        adapter.clear_issue("ws-1")
    adapter.swap_identity("ws-1")
    resolved = adapter.resolve(operation_context(), descriptor)
    assert resolved.handle is None


def test_root_cleanup_is_bounded_and_never_expands_after_divergence() -> None:
    adapter = InMemoryWorkspaceRootAdapter()
    descriptor = adapter.provision(create_context(), "ws-1")
    adapter.seed_entries("ws-1", entries=5, bytes_count=50)
    partial = adapter.cleanup(operation_context(), descriptor, maximum_entries=2)
    assert partial.processed_entries == 2
    assert partial.checkpoint is not None
    adapter.set_issue("ws-1", "SWAPPED")
    failed = adapter.cleanup(operation_context(), descriptor, maximum_entries=100, checkpoint=partial.checkpoint)
    assert failed.effect_state.value == "UNKNOWN"

