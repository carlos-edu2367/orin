from __future__ import annotations

from io import BytesIO

from agentos.filesystem.in_memory import InMemoryFilesystemAdapter, InMemoryWorkspaceRootResolver
from agentos.filesystem.models import Atomicity, FilesystemError, FilesystemLimits, FilesystemOperationContext, OpaqueFilesystemHandle, WorkspacePath, WriteMode
from agentos.filesystem.service import FilesystemService


def test_events_are_post_fact_minimal_and_never_contain_content_or_physical_details() -> None:
    ctx = FilesystemOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    fs = FilesystemService(InMemoryFilesystemAdapter(), InMemoryWorkspaceRootResolver(), handle_validator=lambda handle, **_: True)
    result = fs.write(operation_id="write", context=ctx, lease_id="lease", resource_handle=OpaqueFilesystemHandle("h", "lease"), path=WorkspacePath.from_string("secret.txt"), source=BytesIO(b"top-secret"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(100, 3, 2), idempotency_key="write")
    assert not isinstance(result, FilesystemError)
    event = fs.event_sink.events[-1]
    assert event.event_type == "FilesystemEntryCreated"
    serialized = repr(event)
    assert "top-secret" not in serialized
    assert "physical" not in serialized.lower()
    assert "native" not in serialized.lower()
