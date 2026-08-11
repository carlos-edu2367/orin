from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from agentos.workspaces.models import (
    AcquireWorkspaceLease,
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    RecordWorkspaceUsage,
    ReserveWorkspaceUsage,
    WorkspaceOperationBudget,
    WorkspaceOperationContext,
    WorkspacePermission,
    WorkspaceQuota,
    WorkspaceError,
    InspectWorkspace,
)
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import WorkspaceManagerService


def service_and_lease():
    service = WorkspaceManagerService(InMemoryWorkspaceRegistry(), InMemoryWorkspaceRootAdapter())
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(100, 10, 50, 2, 4, 20), idempotency_key="create"))
    context = WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    active = service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "activate"))
    lease = service.acquire_lease(AcquireWorkspaceLease("lease", WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.lease", "user:user-1"), (WorkspacePermission.WRITE,), __import__("datetime").timedelta(minutes=1), WorkspaceOperationBudget(100, 10, 2), active.version, active.root_descriptor.root_identity, "lease"))
    return service, lease


def reserve(service, lease, *, key: str, bytes_count: int):
    return service.reserve_usage(ReserveWorkspaceUsage("reserve", lease.context, lease.lease_id, lease.fencing_token, bytes_count, 1, bytes_count, 1, service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(lease.context)).version, key))


def test_reservation_happens_before_effect_and_accounting_happens_after_effect() -> None:
    service, lease = service_and_lease()
    reservation = reserve(service, lease, key="r1", bytes_count=40)
    assert reservation.bytes_reserved == 40
    recorded = service.record_usage(RecordWorkspaceUsage("record", lease.context, lease.lease_id, reservation.reservation_id, lease.fencing_token, 40, 1, service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(lease.context)).version, "record"))
    assert recorded.accounted_bytes == 40
    assert recorded.reserved_bytes == 0


def test_concurrent_reservations_cannot_exceed_maximum_and_divergent_usage_blocks_new_reserve() -> None:
    service, lease = service_and_lease()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: reserve(service, lease, key=f"r{index}", bytes_count=30), range(4)))
    assert sum(not isinstance(result, WorkspaceError) for result in results) <= 3
    service.mark_usage_divergent("ws-1")
    blocked = reserve(service, lease, key="divergent", bytes_count=1)
    assert isinstance(blocked, WorkspaceError)
    assert blocked.code.value == "QUOTA_DIVERGENT"


def test_quota_rejection_emits_sanitized_workspace_quota_exceeded_fact() -> None:
    service, lease = service_and_lease()
    blocked = reserve(service, lease, key="too-large", bytes_count=101)
    assert isinstance(blocked, WorkspaceError)
    assert blocked.code.value == "QUOTA_EXCEEDED"
    assert any(event.event_type == "WorkspaceQuotaExceeded" for event in service.event_sink.events)


def test_concurrent_entry_reservations_count_against_outstanding_reservations() -> None:
    service, lease = service_and_lease()
    current = service.registry.get_by_id("ws-1")
    service.registry.replace(replace(current, quota=replace(current.quota, maximum_entries=2), version=current.version + 1), expected_version=current.version)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: reserve(service, lease, key=f"entries-{index}", bytes_count=1), range(4)))
    assert sum(not isinstance(result, WorkspaceError) for result in results) == 2
