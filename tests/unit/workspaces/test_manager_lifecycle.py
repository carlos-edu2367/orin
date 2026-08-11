from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agentos.events.models import DataClassification
from agentos.workspaces.models import (
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    FilesystemObjectIdentity,
    InspectWorkspace,
    WorkspaceError,
    WorkspaceQuota,
    WorkspaceState,
)
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import InMemoryWorkspaceEventSink, WorkspaceManagerService


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 7, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def create_command(requested: str | None = "ws-1", *, key: str = "create-1", actor: str = "user:user-1") -> CreateWorkspace:
    context = CreateWorkspaceContext("user-1", requested, "agent-1", "exec-1", "corr-1", "workspace.create", actor)
    return CreateWorkspace("op-create", context, "Project", WorkspaceQuota(1000, 10, 500, 4, 2, 100), classification=DataClassification.CONFIDENTIAL, idempotency_key=key)


def build() -> tuple[WorkspaceManagerService, InMemoryWorkspaceRootAdapter, Clock, InMemoryWorkspaceEventSink]:
    clock = Clock()
    sink = InMemoryWorkspaceEventSink()
    adapter = InMemoryWorkspaceRootAdapter()
    service = WorkspaceManagerService(InMemoryWorkspaceRegistry(), adapter, clock=clock, event_sink=sink)
    return service, adapter, clock, sink


def activate_command(result) -> ActivateWorkspace:
    context = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-1", result.workspace_id, "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1"
    )
    return ActivateWorkspace("op-activate", context, result.version, result.root_descriptor.root_identity, "activate-1")


def test_create_persists_ownership_before_root_and_activate_confirms_identity() -> None:
    service, adapter, _, sink = build()
    created = service.create(create_command())
    assert created.state is WorkspaceState.PROVISIONING
    assert created.root_descriptor is not None
    activated = service.activate(activate_command(created))
    assert activated.state is WorkspaceState.ACTIVE
    assert [event.event_type for event in sink.events] == ["WorkspaceProvisioningStarted", "WorkspaceActivated"]
    for event in sink.events:
        assert "root" not in repr(event.payload).lower()
        assert "path" not in repr(event.payload).lower()


def test_create_retry_is_idempotent_and_actor_or_root_identity_mismatch_is_rejected() -> None:
    service, _, _, _ = build()
    first = service.create(create_command())
    assert service.create(create_command()) == first
    rejected = service.create(create_command("ws-2", actor="user:other", key="create-2"))
    assert isinstance(rejected, WorkspaceError)
    assert rejected.code.value == "UNAUTHORIZED"
    context = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1"
    )
    bad = ActivateWorkspace("op-activate-2", context, first.version, FilesystemObjectIdentity("wrong"), "activate-2")
    mismatch = service.activate(bad)
    assert isinstance(mismatch, WorkspaceError)
    assert mismatch.code.value == "ROOT_MISMATCH"


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkspaceState.PROVISIONING, WorkspaceState.ARCHIVED),
        (WorkspaceState.ACTIVE, WorkspaceState.ARCHIVED),
        (WorkspaceState.SUSPENDED, WorkspaceState.ACTIVE),
        (WorkspaceState.ARCHIVED, WorkspaceState.ACTIVE),
        (WorkspaceState.DELETED, WorkspaceState.ACTIVE),
    ],
)
def test_prohibited_transitions_are_rejected(source: WorkspaceState, target: WorkspaceState) -> None:
    service, _, _, _ = build()
    created = service.create(create_command())
    if source is WorkspaceState.ACTIVE:
        current = service.activate(activate_command(created))
    else:
        current = replace(created, state=source)
        service.registry._records[current.workspace_id] = current
    context = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-1", current.workspace_id, "agent-1", "exec-1", "corr-1", "workspace.admin", "user:user-1"
    )
    result = service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace(
        "op-transition", context, target, current.version, datetime.now(timezone.utc) + timedelta(minutes=1), "test", "transition-1"
    ))
    assert isinstance(result, WorkspaceError)
    assert result.code.value == "STATE_REJECTED"


def test_inspect_is_authorized_and_does_not_expose_root_value() -> None:
    service, _, _, _ = build()
    created = service.create(create_command())
    context = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-1", created.workspace_id, "agent-1", "exec-1", "corr-1", "workspace.read", "user:user-1"
    )
    snapshot = service.inspect(InspectWorkspace(context))
    assert snapshot.workspace_id == created.workspace_id
    assert "internal" not in repr(snapshot)
    other = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-2", created.workspace_id, "agent-2", "exec-2", "corr-2", "workspace.read", "user:user-2"
    )
    denied = service.inspect(InspectWorkspace(other))
    assert isinstance(denied, WorkspaceError)
    assert denied.code.value == "NOT_FOUND"


def test_all_non_destructive_allowed_lifecycle_transitions_are_explicit() -> None:
    service, _, _, _ = build()
    created = service.create(create_command())
    active = service.activate(activate_command(created))
    context = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-1", active.workspace_id, "agent-1", "exec-1", "corr-1", "workspace.admin", "user:user-1"
    )
    deadline = datetime.now(timezone.utc) + timedelta(minutes=1)
    suspended = service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("suspend", context, "SUSPENDING", active.version, deadline, "pause", "suspend"))
    suspended = service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("suspended", context, "SUSPENDED", suspended.version, deadline, "pause", "suspended"))
    archiving = service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("archive", context, "ARCHIVING", suspended.version, deadline, "archive", "archive"))
    archived = service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("archived", context, "ARCHIVED", archiving.version, deadline, "archive", "archived"))
    assert archived.state is WorkspaceState.ARCHIVED
