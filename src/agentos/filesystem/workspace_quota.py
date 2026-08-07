from __future__ import annotations

from agentos.workspaces.models import (
    RecordWorkspaceUsage,
    ReleaseQuotaReservation,
    ReserveWorkspaceUsage,
    WorkspaceOperationBudget,
    WorkspaceOperationContext,
)


class WorkspaceQuotaAdapter:
    """Maps Filesystem reservations to the authoritative RFC 603 quota port."""

    def __init__(self, workspace_manager, resource_manager=None) -> None:
        self.workspace_manager = workspace_manager
        self.resource_manager = resource_manager

    def _workspace_lease_id(self, lease_id: str) -> str:
        if self.resource_manager is None:
            return lease_id
        lease = self.resource_manager._leases.get(lease_id)
        return getattr(lease, "workspace_lease_id", None) or lease_id

    @staticmethod
    def _context(context):
        return WorkspaceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, "workspace.resource", context.actor)

    def reserve(self, context, lease_id, bytes_count, entries_count, depth, maximum_file_bytes, operation_id, idempotency_key):
        current = self.workspace_manager.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(self._context(context)))
        if hasattr(current, "code"):
            return current
        from agentos.workspaces.models import FencingToken
        lease_id = self._workspace_lease_id(lease_id)
        lease = self.workspace_manager._lease_for(self._context(context), lease_id)
        if hasattr(lease, "code"):
            return lease
        return self.workspace_manager.reserve_usage(ReserveWorkspaceUsage(operation_id, self._context(context), lease_id, lease.fencing_token, bytes_count, entries_count, maximum_file_bytes, depth, current.version, idempotency_key))

    def record(self, reservation, context, lease_id, bytes_effective, entries_effective, operation_id):
        current = self.workspace_manager.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(self._context(context)))
        lease_id = self._workspace_lease_id(lease_id)
        lease = self.workspace_manager._lease_for(self._context(context), lease_id)
        if hasattr(current, "code"):
            return current
        if hasattr(lease, "code"):
            return lease
        return self.workspace_manager.record_usage(RecordWorkspaceUsage(operation_id, self._context(context), lease_id, reservation.reservation_id, lease.fencing_token, bytes_effective, entries_effective, current.version, operation_id + ":record"))

    def release(self, reservation, context, lease_id, operation_id):
        lease_id = self._workspace_lease_id(lease_id)
        lease = self.workspace_manager._lease_for(self._context(context), lease_id)
        if hasattr(lease, "code"):
            return lease
        return self.workspace_manager.release_reservation(ReleaseQuotaReservation(operation_id, self._context(context), lease_id, reservation.reservation_id, lease.fencing_token, operation_id + ":release"))


__all__ = ["WorkspaceQuotaAdapter"]
