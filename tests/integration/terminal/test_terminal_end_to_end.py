from __future__ import annotations

from datetime import timedelta

from agentos.events.models import DataClassification
from agentos.persistence.in_memory import InMemoryTransactionalPersistence
from agentos.resources.models import ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.terminal.models import CreateTerminalSession, TerminalError, TerminalLimits, TerminalOperationContext
from agentos.terminal.persistence import TerminalPersistenceJournal
from agentos.terminal.reference import ReferenceTerminalAdapter
from agentos.terminal.service import TerminalService
from agentos.workspaces.models import ActivateWorkspace, CreateWorkspace, CreateWorkspaceContext, InspectWorkspace, WorkspaceOperationContext, WorkspaceQuota
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import WorkspaceManagerService


def context(workspace: str = "ws-1") -> TerminalOperationContext:
    return TerminalOperationContext("u", workspace, "a", "e", "c", "terminal.session", "agent:a")


def limits() -> TerminalLimits:
    return TerminalLimits(timedelta(minutes=5), timedelta(seconds=5), 1, 1024, timedelta(seconds=5), 64, 64, 64, "network:denied")


def test_resource_workspace_terminal_persistence_and_outbox_compose() -> None:
    workspace_manager = WorkspaceManagerService(InMemoryWorkspaceRegistry(), InMemoryWorkspaceRootAdapter())
    create_context = CreateWorkspaceContext("u", "ws-1", "a", "e", "c", "workspace.create", "user:u")
    created = workspace_manager.create(CreateWorkspace("create-ws", create_context, "Project", WorkspaceQuota(1000, 10, 500, 4, 2, 100), classification=DataClassification.INTERNAL, idempotency_key="ws-key"))
    workspace_context = WorkspaceOperationContext("u", "ws-1", "a", "e", "c", "workspace.activate", "user:u")
    active = workspace_manager.activate(ActivateWorkspace("activate-ws", workspace_context, created.version, created.root_descriptor.root_identity, "activate-key"))
    assert active.state.value == "ACTIVE"
    terminal_context = context()
    resource_manager = ResourceManagerService(workspace_manager=workspace_manager)
    resource_context = ResourceOperationContext(*terminal_context.scope_key())
    lease = resource_manager.acquire(ResourceLeaseRequest("request-terminal", resource_context, ResourceType.TERMINAL, (ResourceCapability.TERMINAL_SESSION,), (ResourceCapability.TERMINAL_SESSION, ResourceCapability.INSPECT), ResourceBudget(10, 100), timedelta(minutes=5), "terminal-lease"))
    persistence = InMemoryTransactionalPersistence()
    journal = TerminalPersistenceJournal(persistence)
    service = TerminalService(resource_manager=resource_manager, workspace_manager=workspace_manager, adapter=ReferenceTerminalAdapter(), persistence_journal=journal)
    snapshot = service.create(CreateTerminalSession("create-terminal", terminal_context, lease.lease_id, __import__("agentos.filesystem.models", fromlist=["WorkspacePath"]).WorkspacePath.root(), "shell:reference", (), limits(), "terminal-key"))
    assert snapshot.id
    assert journal.load(terminal_context, snapshot.id) == snapshot
    assert len(persistence.confirmed_outbox()) == 1
    foreign = service.create(CreateTerminalSession("create-foreign", context("ws-2"), lease.lease_id, __import__("agentos.filesystem.models", fromlist=["WorkspacePath"]).WorkspacePath.root(), "shell:reference", (), limits(), "foreign-key"))
    assert isinstance(foreign, TerminalError)
