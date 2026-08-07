from __future__ import annotations

from datetime import timedelta
import pickle

import pytest

from agentos.events import DataClassification
from agentos.filesystem.models import (
    FilesystemEntry,
    FilesystemEntryKind,
    FilesystemError,
    FilesystemErrorCode,
    FilesystemLimits,
    FilesystemOperationContext,
    OpaqueFilesystemHandle,
    WorkspacePath,
)


def context() -> FilesystemOperationContext:
    return FilesystemOperationContext(
        user_id="user-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        execution_id="exec-1",
        correlation_id="corr-1",
        purpose="filesystem.read",
        actor="agent:agent-1",
    )


def test_filesystem_context_requires_all_scope_fields_and_keeps_purpose_bounded() -> None:
    assert context().scope_key() == (
        "user-1", "ws-1", "agent-1", "exec-1", "corr-1", "filesystem.read", "agent:agent-1"
    )
    with pytest.raises(ValueError):
        FilesystemOperationContext("", "ws-1", "agent-1", "exec-1", "corr-1", "p", "agent:agent-1")
    with pytest.raises(ValueError):
        FilesystemOperationContext("u", "ws-1", "a", "e", "c", "x" * 257, "agent:a")


def test_workspace_path_is_relative_and_normalized() -> None:
    path = WorkspacePath.from_segments("docs", "relatorio.txt")
    assert path.segments == ("docs", "relatorio.txt")
    assert path.as_logical_string() == "docs/relatorio.txt"
    assert WorkspacePath.root().segments == ()
    with pytest.raises(ValueError):
        WorkspacePath.from_string("../outside")


def test_public_entries_errors_and_limits_are_bounded_and_non_physical() -> None:
    entry = FilesystemEntry(
        WorkspacePath.from_string("report.txt"), FilesystemEntryKind.FILE, 3, 1,
        DataClassification.INTERNAL,
    )
    assert "physical" not in repr(entry).lower()
    limits = FilesystemLimits(maximum_bytes=10, maximum_entries=2, maximum_depth=1, timeout=timedelta(seconds=1))
    assert limits.maximum_bytes == 10
    error = FilesystemError(FilesystemErrorCode.REJECTED, "path policy", effect_state="NOT_APPLIED")
    assert "path" not in repr(error).lower()
    assert "physical" not in str(error).lower()


def test_filesystem_handles_are_opaque_and_not_serializable() -> None:
    handle = OpaqueFilesystemHandle("handle:1", binding="lease:1")
    assert "handle:1" not in repr(handle)
    with pytest.raises(TypeError):
        pickle.dumps(handle)
