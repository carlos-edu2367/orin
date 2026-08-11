from __future__ import annotations

from agentos.workspaces.models import (
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    WorkspaceOperationContext,
    WorkspaceQuota,
)
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import InMemoryWorkspaceEventSink, WorkspaceManagerService


def test_all_workspace_events_are_minimal_and_emitted_only_after_confirmed_facts() -> None:
    sink = InMemoryWorkspaceEventSink()
    service = WorkspaceManagerService(InMemoryWorkspaceRegistry(), InMemoryWorkspaceRootAdapter(), event_sink=sink)
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(1000, 10, 500, 4, 2, 2), idempotency_key="create"))
    context = WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    active = service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "activate"))
    service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("suspend", context, "SUSPENDING", active.version, __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=1), "pause", "suspend"))
    current = service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context))
    service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("suspended", context, "SUSPENDED", current.version, __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=1), "pause", "suspended"))
    assert {event.event_type for event in sink.events} >= {"WorkspaceProvisioningStarted", "WorkspaceActivated", "WorkspaceSuspended"}
    for event in sink.events:
        serialized = repr(event)
        assert "root" not in serialized.lower()
        assert "path" not in serialized.lower()
        assert "handle" not in serialized.lower()
        assert "Project" not in serialized

