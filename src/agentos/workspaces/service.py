from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock
from typing import Callable

from agentos.events.models import DataClassification, EventEnvelope

from .models import (
    ActivateWorkspace,
    AcquireWorkspaceLease,
    AcquireWorkspaceLock,
    CreateWorkspace,
    CreateWorkspaceContext,
    EffectState,
    InspectWorkspace,
    LeaseState,
    FencingToken,
    ReleaseQuotaReservation,
    ReleaseWorkspaceLease,
    RenewWorkspaceLease,
    ReserveWorkspaceUsage,
    RecordWorkspaceUsage,
    QuotaReservation,
    WorkspaceLease,
    WorkspaceLock,
    LockState,
    UsageReconciliationState,
    TransitionWorkspace,
    WorkspaceError,
    WorkspaceErrorCode,
    WorkspaceOperationContext,
    WorkspaceRecord,
    WorkspaceSnapshot,
    WorkspaceState,
    Retryability,
    DeleteWorkspace,
    ReconcileScope,
    ReconcileWorkspace,
    WorkspaceDeletionReceipt,
    WorkspaceReconciliationReceipt,
)
from .ports import WorkspaceEventSink, WorkspaceRootAdapter
from .registry import InMemoryWorkspaceRegistry
from .security import sanitize_display_name, sanitize_public_reason, validate_actor_binding


class InMemoryWorkspaceEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def append(self, event: EventEnvelope) -> None:
        self.events.append(event)


class WorkspaceManagerService:
    """Reference Workspace authority; physical root semantics stay in the adapter."""

    def __init__(
        self,
        registry: InMemoryWorkspaceRegistry,
        root_adapter: WorkspaceRootAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
        event_sink: WorkspaceEventSink | None = None,
    ) -> None:
        self.registry = registry
        self.root_adapter = root_adapter
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.event_sink = event_sink or InMemoryWorkspaceEventSink()
        self._counter = 0
        self._lock = RLock()
        self._sequences: dict[str, int] = {}
        self._create_ids: dict[tuple[tuple[str, ...], str], str] = {}
        self._leases: dict[str, WorkspaceLease] = {}
        self._lease_idempotency: dict[tuple[tuple[str, ...], str], tuple[str, WorkspaceLease]] = {}
        self._locks: dict[str, WorkspaceLock] = {}
        self._lock_idempotency: dict[tuple[tuple[str, ...], str], tuple[str, WorkspaceLock]] = {}
        self._fence_counter = 0
        self._reservations: dict[str, QuotaReservation] = {}
        self._reservation_idempotency: dict[tuple[tuple[str, ...], str], tuple[str, QuotaReservation]] = {}
        self._suspension_irreversible: set[str] = set()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}:{self._counter}"

    @staticmethod
    def _error(code: WorkspaceErrorCode, *, retryability: Retryability = Retryability.NEVER, effect_state: EffectState = EffectState.NOT_APPLIED, reason: str = "workspace operation failed") -> WorkspaceError:
        return WorkspaceError(code, retryability, effect_state, sanitize_public_reason(reason))

    @staticmethod
    def _snapshot(record: WorkspaceRecord, *, include_usage: bool = True) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            state=record.state,
            classification=record.classification,
            quota=record.quota,
            usage=record.usage if include_usage else None,
            version=record.version,
            policy_version=record.root_descriptor.containment_policy_version if record.root_descriptor else 1,
            root_descriptor=record.root_descriptor,
        )

    def _event(self, event_type: str, context: WorkspaceOperationContext, record: WorkspaceRecord, *, reason: str | None = None) -> None:
        sequence = self._sequences.get(context.execution_id, 0) + 1
        self._sequences[context.execution_id] = sequence
        payload: dict[str, object] = {
            "workspace_id": record.workspace_id,
            "user_id": record.user_id,
            "state": record.state.value,
            "version": record.version,
            "policy_version": record.root_descriptor.containment_policy_version if record.root_descriptor else 1,
            "purpose": "workspace.lifecycle",
        }
        if reason:
            payload["reason_code"] = sanitize_public_reason(reason)
        event = EventEnvelope(
            event_id=self._next("workspace-event"),
            event_type=event_type,
            event_version=1,
            occurred_at=self._now(),
            source="workspaces",
            correlation_id=context.correlation_id,
            causation_id=None,
            sequence=sequence,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            execution_id=context.execution_id,
            agent_id=context.agent_id,
            classification=record.classification,
            payload=payload,
        )
        if getattr(self.registry, "events_are_transactional", False):
            if event_type == "WorkspaceQuotaExceeded":
                recorder = getattr(self.registry, "record_fact", None)
                if recorder is not None:
                    recorder(event, context, record)
            return
        self.event_sink.append(event)

    def _authorize_create(self, context: CreateWorkspaceContext) -> WorkspaceError | None:
        try:
            validate_actor_binding(context.user_id, context.actor, context.agent_id)
        except PermissionError as exc:
            return self._error(WorkspaceErrorCode.UNAUTHORIZED, reason=str(exc))
        if context.purpose != "workspace.create":
            return self._error(WorkspaceErrorCode.UNAUTHORIZED, reason="purpose is not allowed for creation")
        return None

    def _authorize(self, context: WorkspaceOperationContext) -> WorkspaceError | None:
        try:
            validate_actor_binding(context.user_id, context.actor, context.agent_id)
        except PermissionError as exc:
            return self._error(WorkspaceErrorCode.UNAUTHORIZED, reason=str(exc))
        if not context.purpose.startswith("workspace."):
            return self._error(WorkspaceErrorCode.UNAUTHORIZED, reason="purpose is not allowed for Workspace")
        return None

    def create(self, command: CreateWorkspace) -> WorkspaceSnapshot | WorkspaceError:
        denied = self._authorize_create(command.context)
        if denied:
            return denied
        try:
            display_name = sanitize_display_name(command.display_name)
        except ValueError:
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        key = (command.context.scope_key(), command.idempotency_key)
        workspace_id = self._create_ids.get(key)
        if workspace_id is None:
            workspace_id = command.context.requested_workspace_id or self._next("workspace").replace(":", "-", 1)
            self._create_ids[key] = workspace_id
        if command.context.requested_workspace_id != workspace_id:
            context = CreateWorkspaceContext(command.context.user_id, workspace_id, command.context.agent_id, command.context.execution_id, command.context.correlation_id, command.context.purpose, command.context.actor)
            command = replace(command, context=context)
        now = self._now()
        from .models import WorkspaceQuota, WorkspaceUsage, UsageReconciliationState
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            user_id=command.context.user_id,
            display_name=display_name,
            state=WorkspaceState.PROVISIONING,
            root_descriptor=None,
            quota=command.quota,
            configuration_ref=command.configuration_ref,
            classification=DataClassification(command.classification),
            version=1,
            usage=WorkspaceUsage(0, 0, 0, 0, now, UsageReconciliationState.CURRENT),
            created_at=now,
            creation_idempotency_key=command.idempotency_key,
            creation_fingerprint=hashlib.sha256(json.dumps((command.context.user_id, display_name, command.quota, command.configuration_ref, command.classification.value), default=str, sort_keys=True).encode()).hexdigest(),
        )
        created = self.registry.create(command, record)
        if isinstance(created, WorkspaceError):
            return created
        if created.root_descriptor is not None or created.version > 1:
            return self._snapshot(created)
        op_context = WorkspaceOperationContext(command.context.user_id, workspace_id, command.context.agent_id, command.context.execution_id, command.context.correlation_id, "workspace.lifecycle", command.context.actor)
        self._event("WorkspaceProvisioningStarted", op_context, created)
        descriptor = self.root_adapter.provision(command.context, workspace_id)
        if isinstance(descriptor, WorkspaceError):
            failed = replace(created, state=WorkspaceState.FAILED, version=created.version + 1)
            self.registry.replace(failed, expected_version=created.version)
            self._event("WorkspaceRecoveryRequired", op_context, failed, reason="provisioning failed")
            return self._error(WorkspaceErrorCode.RECOVERY_REQUIRED, retryability=Retryability.AFTER_RECONCILIATION, effect_state=EffectState.APPLIED, reason="provisioning requires reconciliation")
        with_root = replace(created, root_descriptor=descriptor, version=created.version + 1)
        stored = self.registry.replace(with_root, expected_version=created.version)
        if isinstance(stored, WorkspaceError):
            return stored
        return self._snapshot(stored)

    def activate(self, command: ActivateWorkspace) -> WorkspaceSnapshot | WorkspaceError:
        denied = self._authorize(command.context)
        if denied:
            return denied
        current = self.registry.get(command.context)
        if current is None:
            return self._error(WorkspaceErrorCode.NOT_FOUND)
        if current.version != command.expected_version:
            return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
        if current.root_descriptor is None or current.root_descriptor.root_identity != command.expected_root_identity:
            return self._error(WorkspaceErrorCode.ROOT_MISMATCH)
        if current.state is WorkspaceState.ACTIVE:
            return self._snapshot(current)
        if current.state is not WorkspaceState.PROVISIONING:
            return self._error(WorkspaceErrorCode.STATE_REJECTED)
        resolved = self.root_adapter.resolve(command.context, current.root_descriptor)
        if resolved.handle is None or resolved.identity != current.root_descriptor.root_identity:
            changed = replace(current, state=WorkspaceState.RECOVERY_REQUIRED, version=current.version + 1)
            self.registry.replace(changed, expected_version=current.version)
            self._event("WorkspaceRecoveryRequired", command.context, changed, reason=resolved.reason or "root is not safe")
            return self._error(WorkspaceErrorCode.ROOT_UNSAFE, retryability=Retryability.AFTER_RECONCILIATION, effect_state=EffectState.APPLIED, reason="root is not safe")
        self.root_adapter.release(resolved.handle)
        activated = replace(current, state=WorkspaceState.ACTIVE, version=current.version + 1, activated_at=self._now())
        stored = self.registry.replace(activated, expected_version=current.version)
        if isinstance(stored, WorkspaceError):
            return stored
        self._event("WorkspaceActivated", command.context, stored)
        return self._snapshot(stored)

    def inspect(self, query: InspectWorkspace) -> WorkspaceSnapshot | WorkspaceError:
        denied = self._authorize(query.context)
        if denied:
            return denied
        current = self.registry.get(query.context)
        if current is None:
            return self._error(WorkspaceErrorCode.NOT_FOUND)
        return self._snapshot(current, include_usage=query.include_usage)

    _TRANSITIONS = {
        (WorkspaceState.ACTIVE, WorkspaceState.SUSPENDING),
        (WorkspaceState.SUSPENDING, WorkspaceState.SUSPENDED),
        (WorkspaceState.SUSPENDING, WorkspaceState.ACTIVE),
        (WorkspaceState.SUSPENDED, WorkspaceState.ARCHIVING),
        (WorkspaceState.ARCHIVING, WorkspaceState.ARCHIVED),
    }

    def transition(self, command: TransitionWorkspace) -> WorkspaceSnapshot | WorkspaceError:
        denied = self._authorize(command.context)
        if denied:
            return denied
        current = self.registry.get(command.context)
        if current is None:
            return self._error(WorkspaceErrorCode.NOT_FOUND)
        if current.version != command.expected_version:
            return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
        target = WorkspaceState(command.target_state)
        if current.state is target:
            return self._snapshot(current)
        if current.state is WorkspaceState.SUSPENDING and target is WorkspaceState.ACTIVE and current.workspace_id in self._suspension_irreversible:
            return self._error(WorkspaceErrorCode.STATE_REJECTED, reason="suspension barrier already revoked leases")
        if current.state is WorkspaceState.SUSPENDING and target is WorkspaceState.SUSPENDED:
            active_leases = [lease for lease in self._leases.values() if lease.workspace_id == current.workspace_id and lease.state is LeaseState.ACTIVE]
            if active_leases:
                if self._now() < command.drain_deadline:
                    return self._error(WorkspaceErrorCode.STATE_REJECTED, retryability=Retryability.SAFE, reason="active leases are still draining")
                self._revoke_active_leases(current.workspace_id)
                self._suspension_irreversible.add(current.workspace_id)
                usage = replace(current.usage, active_leases=0, measured_at=self._now())
                drained = replace(current, usage=usage, version=current.version + 1)
                stored_drained = self.registry.replace(drained, expected_version=current.version)
                if isinstance(stored_drained, WorkspaceError):
                    return stored_drained
                current = stored_drained
        if (current.state, target) not in self._TRANSITIONS:
            return self._error(WorkspaceErrorCode.STATE_REJECTED)
        updated = replace(
            current,
            state=target,
            version=current.version + 1,
            archived_at=self._now() if target is WorkspaceState.ARCHIVED else current.archived_at,
        )
        stored = self.registry.replace(updated, expected_version=current.version)
        if isinstance(stored, WorkspaceError):
            return stored
        if target is WorkspaceState.SUSPENDED:
            self._event("WorkspaceSuspended", command.context, stored, reason=command.reason)
        elif target is WorkspaceState.ARCHIVED:
            self._event("WorkspaceArchived", command.context, stored, reason=command.reason)
        return self._snapshot(stored)

    def acquire_lock(self, command: AcquireWorkspaceLock) -> WorkspaceLock | WorkspaceError:
        denied = self._authorize(command.context)
        if denied:
            return denied
        if command.requested_duration <= timedelta(0) or command.requested_duration > timedelta(hours=1):
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        current = self.registry.get(command.context)
        if current is None:
            return self._error(WorkspaceErrorCode.NOT_FOUND)
        if current.version != command.expected_workspace_version:
            return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
        fingerprint = hashlib.sha256(json.dumps((command.operation_id, command.expected_workspace_version, command.requested_duration.total_seconds()), default=str).encode()).hexdigest()
        key = (command.context.scope_key(), command.idempotency_key)
        with self._lock:
            prior = self._lock_idempotency.get(key)
            if prior is not None:
                return prior[1] if prior[0] == fingerprint else self._error(WorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
            self._fence_counter += 1
            lock = WorkspaceLock(self._next("lock"), command.context.workspace_id, command.context, current.version, FencingToken(self._fence_counter), self._now() + command.requested_duration)
            self._locks[lock.lock_id] = lock
            self._lock_idempotency[key] = (fingerprint, lock)
            return lock

    def release_lock(self, lock_id: str, context: WorkspaceOperationContext, fencing_token: FencingToken) -> WorkspaceLock | WorkspaceError:
        denied = self._authorize(context)
        if denied:
            return denied
        with self._lock:
            lock = self._locks.get(lock_id)
            if lock is None or lock.context.scope_key() != context.scope_key():
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if lock.fencing_token != fencing_token:
                return self._error(WorkspaceErrorCode.FENCE_REJECTED)
            if lock.state is LockState.RELEASED:
                return lock
            released = replace(lock, state=LockState.RELEASED)
            self._locks[lock_id] = released
            return released

    def assert_fence(self, context: WorkspaceOperationContext, fencing_token: FencingToken) -> None | WorkspaceError:
        with self._lock:
            active = [lock for lock in self._locks.values() if lock.workspace_id == context.workspace_id and lock.state is LockState.ACTIVE]
            if not active or any(lock.fencing_token != fencing_token for lock in active):
                return self._error(WorkspaceErrorCode.FENCE_REJECTED)
        return None

    def _root_for(self, context: WorkspaceOperationContext):
        current = self.registry.get(context)
        if current is None:
            return None, self._error(WorkspaceErrorCode.NOT_FOUND)
        if current.root_descriptor is None:
            return None, self._error(WorkspaceErrorCode.ROOT_UNSAFE)
        return current, None

    def acquire_lease(self, request: AcquireWorkspaceLease) -> WorkspaceLease | WorkspaceError:
        denied = self._authorize(request.context)
        if denied:
            return denied
        if request.requested_duration <= timedelta(0) or request.requested_duration > timedelta(hours=1):
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        fingerprint = hashlib.sha256(json.dumps((request.operation_id, tuple(request.permissions), request.requested_duration.total_seconds(), request.expected_workspace_version, repr(request.expected_root_identity)), default=str).encode()).hexdigest()
        key = (request.context.scope_key(), request.idempotency_key)
        with self._lock:
            prior = self._lease_idempotency.get(key)
            if prior is not None:
                return prior[1] if prior[0] == fingerprint else self._error(WorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
            current, error = self._root_for(request.context)
            if error:
                return error
            if current.state is not WorkspaceState.ACTIVE:
                return self._error(WorkspaceErrorCode.STATE_REJECTED)
            if current.version != request.expected_workspace_version:
                return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
            if current.root_descriptor.root_identity != request.expected_root_identity:
                return self._error(WorkspaceErrorCode.ROOT_MISMATCH)
            if current.usage.reconciliation_state is not UsageReconciliationState.CURRENT:
                return self._error(WorkspaceErrorCode.QUOTA_DIVERGENT, retryability=Retryability.AFTER_RECONCILIATION)
            if current.usage.active_leases >= current.quota.maximum_active_leases:
                self._quota_exceeded(request.context, current, "active lease quota exceeded")
                return self._error(WorkspaceErrorCode.QUOTA_EXCEEDED)
            resolved = self.root_adapter.resolve(request.context, current.root_descriptor)
            latest = self.registry.get(request.context)
            if resolved.handle is None or resolved.identity != current.root_descriptor.root_identity or latest is None or latest.version != current.version or latest.root_descriptor.root_identity != current.root_descriptor.root_identity or latest.state is not WorkspaceState.ACTIVE:
                if resolved.handle is not None:
                    self.root_adapter.release(resolved.handle)
                return self._error(WorkspaceErrorCode.ROOT_MISMATCH if resolved.handle is not None else WorkspaceErrorCode.ROOT_UNSAFE, retryability=Retryability.AFTER_RECONCILIATION)
            updated_usage = replace(current.usage, active_leases=current.usage.active_leases + 1, measured_at=self._now())
            updated = replace(current, usage=updated_usage, version=current.version + 1)
            stored = self.registry.replace(updated, expected_version=current.version)
            if isinstance(stored, WorkspaceError):
                self.root_adapter.release(resolved.handle)
                return stored
            self._fence_counter += 1
            lease = WorkspaceLease(self._next("lease"), request.context.workspace_id, request.context, tuple(request.permissions), request.budget, resolved.handle, current.root_descriptor.root_identity, stored.version, FencingToken(self._fence_counter), self._now(), self._now() + request.requested_duration)
            self._leases[lease.lease_id] = lease
            self._lease_idempotency[key] = (fingerprint, lease)
            return lease

    def _lease_for(self, context: WorkspaceOperationContext, lease_id: str) -> WorkspaceLease | WorkspaceError:
        lease = self._leases.get(lease_id)
        if lease is None or lease.context.scope_key() != context.scope_key():
            return self._error(WorkspaceErrorCode.UNAUTHORIZED)
        if lease.state is LeaseState.RELEASED or lease.state is LeaseState.REVOKING:
            return self._error(WorkspaceErrorCode.LEASE_REVOKED)
        if self._now() >= lease.expires_at:
            self._leases[lease_id] = replace(lease, state=LeaseState.EXPIRED)
            return self._error(WorkspaceErrorCode.LEASE_EXPIRED)
        return lease

    def renew_lease(self, request: RenewWorkspaceLease) -> WorkspaceLease | WorkspaceError:
        denied = self._authorize(request.context)
        if denied:
            return denied
        if request.requested_extension <= timedelta(0) or request.requested_extension > timedelta(hours=1):
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        with self._lock:
            lease = self._lease_for(request.context, request.lease_id)
            if isinstance(lease, WorkspaceError):
                return lease
            if lease.fencing_token != request.fencing_token or lease.expires_at != request.expected_expires_at:
                return self._error(WorkspaceErrorCode.FENCE_REJECTED)
            current = self.registry.get(request.context)
            if current is None:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if current.state is not WorkspaceState.ACTIVE:
                return self._error(WorkspaceErrorCode.STATE_REJECTED)
            if current.usage.reconciliation_state is not UsageReconciliationState.CURRENT:
                return self._error(WorkspaceErrorCode.QUOTA_DIVERGENT, retryability=Retryability.AFTER_RECONCILIATION)
            if current.version != request.expected_workspace_version or current.root_descriptor is None or current.root_descriptor.root_identity != request.expected_root_identity:
                return self._error(WorkspaceErrorCode.VERSION_CONFLICT if current.version != request.expected_workspace_version else WorkspaceErrorCode.ROOT_MISMATCH)
            resolved = self.root_adapter.resolve(request.context, current.root_descriptor)
            if resolved.handle is None or resolved.identity != lease.root_identity:
                return self._error(WorkspaceErrorCode.ROOT_MISMATCH, retryability=Retryability.AFTER_RECONCILIATION)
            self.root_adapter.release(lease.root_handle_ref)
            renewed = replace(lease, root_handle_ref=resolved.handle, workspace_version=current.version, expires_at=self._now() + request.requested_extension)
            self._leases[lease.lease_id] = renewed
            return renewed

    def release_lease(self, request: ReleaseWorkspaceLease) -> WorkspaceLease | WorkspaceError:
        denied = self._authorize(request.context)
        if denied:
            return denied
        with self._lock:
            lease = self._leases.get(request.lease_id)
            if lease is None or lease.context.scope_key() != request.context.scope_key():
                return self._error(WorkspaceErrorCode.UNAUTHORIZED)
            if lease.fencing_token != request.fencing_token:
                return self._error(WorkspaceErrorCode.FENCE_REJECTED)
            if lease.state is LeaseState.RELEASED:
                return lease
            current = self.registry.get(request.context)
            if current is not None and lease.state is LeaseState.ACTIVE:
                usage = replace(current.usage, active_leases=max(0, current.usage.active_leases - 1), measured_at=self._now())
                updated = replace(current, usage=usage, version=current.version + 1)
                stored = self.registry.replace(updated, expected_version=current.version)
                if isinstance(stored, WorkspaceError):
                    return stored
            self.root_adapter.release(lease.root_handle_ref)
            released = replace(lease, state=LeaseState.RELEASED)
            self._leases[lease.lease_id] = released
            return released

    def _fenced_lease(self, context: WorkspaceOperationContext, lease_id: str, token) -> WorkspaceLease | WorkspaceError:
        lease = self._lease_for(context, lease_id)
        if isinstance(lease, WorkspaceError):
            return lease
        if lease.fencing_token != token:
            return self._error(WorkspaceErrorCode.FENCE_REJECTED)
        return lease

    def reserve_usage(self, request: ReserveWorkspaceUsage) -> QuotaReservation | WorkspaceError:
        denied = self._authorize(request.context)
        if denied:
            return denied
        if any(value < 0 for value in (request.bytes_requested, request.entries_requested, request.maximum_file_bytes, request.depth)):
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        key = (request.context.scope_key(), request.idempotency_key)
        fingerprint = hashlib.sha256(json.dumps((request.lease_id, request.bytes_requested, request.entries_requested, request.maximum_file_bytes, request.depth, request.expected_workspace_version), default=str).encode()).hexdigest()
        with self._lock:
            prior = self._reservation_idempotency.get(key)
            if prior is not None:
                return prior[1] if prior[0] == fingerprint else self._error(WorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
            lease = self._fenced_lease(request.context, request.lease_id, request.fencing_token)
            if isinstance(lease, WorkspaceError):
                return lease
            current = self.registry.get(request.context)
            if current is None:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if current.version != request.expected_workspace_version:
                return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
            if current.usage.reconciliation_state is not UsageReconciliationState.CURRENT:
                return self._error(WorkspaceErrorCode.QUOTA_DIVERGENT, retryability=Retryability.AFTER_RECONCILIATION)
            if request.maximum_file_bytes > current.quota.maximum_file_bytes or request.depth > current.quota.maximum_depth:
                self._quota_exceeded(request.context, current, "operation budget exceeds workspace quota")
                return self._error(WorkspaceErrorCode.QUOTA_EXCEEDED)
            if current.usage.accounted_bytes + current.usage.reserved_bytes + request.bytes_requested > current.quota.maximum_bytes:
                self._quota_exceeded(request.context, current, "reserved bytes exceed workspace quota")
                return self._error(WorkspaceErrorCode.QUOTA_EXCEEDED)
            if current.usage.accounted_entries + current.usage.reserved_entries + request.entries_requested > current.quota.maximum_entries:
                self._quota_exceeded(request.context, current, "reserved entries exceed workspace quota")
                return self._error(WorkspaceErrorCode.QUOTA_EXCEEDED)
            usage = replace(current.usage, reserved_bytes=current.usage.reserved_bytes + request.bytes_requested, reserved_entries=current.usage.reserved_entries + request.entries_requested, measured_at=self._now())
            updated = replace(current, usage=usage, version=current.version + 1)
            stored = self.registry.replace(updated, expected_version=current.version)
            if isinstance(stored, WorkspaceError):
                return stored
            reservation = QuotaReservation(self._next("reservation"), current.workspace_id, lease.lease_id, request.bytes_requested, request.entries_requested, request.depth, stored.version, self._now() + lease.budget.duration)
            self._reservations[reservation.reservation_id] = reservation
            self._reservation_idempotency[key] = (fingerprint, reservation)
            return reservation

    def record_usage(self, request: RecordWorkspaceUsage):
        denied = self._authorize(request.context)
        if denied:
            return denied
        with self._lock:
            lease = self._fenced_lease(request.context, request.lease_id, request.fencing_token)
            if isinstance(lease, WorkspaceError):
                return lease
            reservation = self._reservations.get(request.reservation_id)
            if reservation is None or reservation.lease_id != lease.lease_id:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if request.bytes_effective < 0 or request.entries_effective < 0 or request.bytes_effective > reservation.bytes_reserved or request.entries_effective > reservation.entries_reserved:
                return self._error(WorkspaceErrorCode.QUOTA_EXCEEDED)
            current = self.registry.get(request.context)
            if current is None:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if current.version != request.expected_workspace_version:
                return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
            if current.usage.accounted_bytes + request.bytes_effective > current.quota.maximum_bytes or current.usage.accounted_entries + request.entries_effective > current.quota.maximum_entries:
                self._quota_exceeded(request.context, current, "recorded usage exceeds workspace quota")
                divergent = replace(current, usage=replace(current.usage, reconciliation_state=UsageReconciliationState.DIVERGENT), version=current.version + 1)
                self.registry.replace(divergent, expected_version=current.version)
                return self._error(WorkspaceErrorCode.QUOTA_DIVERGENT, retryability=Retryability.AFTER_RECONCILIATION, effect_state=EffectState.APPLIED)
            usage = replace(current.usage, reserved_bytes=max(0, current.usage.reserved_bytes - reservation.bytes_reserved), reserved_entries=max(0, current.usage.reserved_entries - reservation.entries_reserved), accounted_bytes=current.usage.accounted_bytes + request.bytes_effective, accounted_entries=current.usage.accounted_entries + request.entries_effective, measured_at=self._now())
            updated = replace(current, usage=usage, version=current.version + 1)
            stored = self.registry.replace(updated, expected_version=current.version)
            if isinstance(stored, WorkspaceError):
                return stored
            self._reservations[reservation.reservation_id] = replace(reservation, state=EffectState.APPLIED, bytes_reserved=0, entries_reserved=0)
            return stored.usage

    def release_reservation(self, request: ReleaseQuotaReservation):
        denied = self._authorize(request.context)
        if denied:
            return denied
        with self._lock:
            lease = self._fenced_lease(request.context, request.lease_id, request.fencing_token)
            if isinstance(lease, WorkspaceError):
                return lease
            reservation = self._reservations.get(request.reservation_id)
            if reservation is None or reservation.lease_id != lease.lease_id:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            current = self.registry.get(request.context)
            if current is None:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            usage = replace(current.usage, reserved_bytes=max(0, current.usage.reserved_bytes - reservation.bytes_reserved), reserved_entries=max(0, current.usage.reserved_entries - reservation.entries_reserved), measured_at=self._now())
            updated = replace(current, usage=usage, version=current.version + 1)
            stored = self.registry.replace(updated, expected_version=current.version)
            if isinstance(stored, WorkspaceError):
                return stored
            self._reservations[reservation.reservation_id] = replace(reservation, state=EffectState.NOT_APPLIED, bytes_reserved=0, entries_reserved=0)
            return stored.usage

    def mark_usage_divergent(self, workspace_id: str) -> None:
        current = self.registry.get_by_id(workspace_id)
        if current is None:
            return
        updated = replace(current, usage=replace(current.usage, reconciliation_state=UsageReconciliationState.DIVERGENT), version=current.version + 1)
        self.registry.replace(updated, expected_version=current.version)

    def _quota_exceeded(self, context: WorkspaceOperationContext, record: WorkspaceRecord, reason: str) -> None:
        self._event("WorkspaceQuotaExceeded", context, record, reason=reason)

    def _revoke_active_leases(self, workspace_id: str) -> int:
        revoked = 0
        for lease_id, lease in tuple(self._leases.items()):
            if lease.workspace_id != workspace_id or lease.state is not LeaseState.ACTIVE:
                continue
            self.root_adapter.release(lease.root_handle_ref)
            self._leases[lease_id] = replace(lease, state=LeaseState.RELEASED)
            revoked += 1
        return revoked

    def _deletion_receipt(self, record: WorkspaceRecord, fence: FencingToken, effect: EffectState, categories: tuple[str, ...], *, checkpoint: str | None = None, reason: str | None = None) -> WorkspaceDeletionReceipt:
        return WorkspaceDeletionReceipt(record.workspace_id, record.state, record.version, fence, effect, categories, checkpoint, sanitize_public_reason(reason) if reason else None)

    def _continue_cleanup(self, context: WorkspaceOperationContext, record: WorkspaceRecord, fence: FencingToken) -> WorkspaceDeletionReceipt:
        descriptor = record.root_descriptor
        if descriptor is None:
            failed = replace(record, state=WorkspaceState.RECOVERY_REQUIRED, version=record.version + 1)
            stored = self.registry.replace(failed, expected_version=record.version)
            if not isinstance(stored, WorkspaceError):
                self._event("WorkspaceRecoveryRequired", context, stored, reason="deletion root missing")
                record = stored
            return self._deletion_receipt(record, fence, EffectState.UNKNOWN, ("ROOT",), reason="root missing")
        checkpoint = record.deletion_checkpoint
        result = self.root_adapter.cleanup(context, descriptor, maximum_entries=32, checkpoint=checkpoint)
        if result.effect_state is EffectState.UNKNOWN or result.health is not getattr(__import__("agentos.workspaces.models", fromlist=["RootHealth"]), "RootHealth").READY:
            failed = replace(record, state=WorkspaceState.RECOVERY_REQUIRED, version=record.version + 1)
            stored = self.registry.replace(failed, expected_version=record.version)
            if isinstance(stored, WorkspaceError):
                stored = record
            else:
                self._event("WorkspaceRecoveryRequired", context, stored, reason=result.reason or "cleanup is uncertain")
            return self._deletion_receipt(stored, fence, EffectState.UNKNOWN, ("ROOT", "METADATA"), checkpoint=result.checkpoint, reason=result.reason or "cleanup is uncertain")
        if result.checkpoint is not None:
            checkpointed = replace(record, deletion_checkpoint=result.checkpoint, version=record.version + 1)
            stored = self.registry.replace(checkpointed, expected_version=record.version)
            if isinstance(stored, WorkspaceError):
                return self._deletion_receipt(record, fence, EffectState.UNKNOWN, ("ROOT",), checkpoint=result.checkpoint, reason="delete checkpoint commit is indeterminate")
            return self._deletion_receipt(stored, fence, EffectState.APPLIED, ("ROOT", "METADATA", "ARTIFACTS", "TEMPORARIES", "AUDIT"), checkpoint=result.checkpoint)
        finalized = getattr(self.root_adapter, "finalize_delete", lambda workspace_id, descriptor: False)(record.workspace_id, descriptor)
        if not finalized:
            failed = replace(record, state=WorkspaceState.RECOVERY_REQUIRED, version=record.version + 1)
            stored = self.registry.replace(failed, expected_version=record.version)
            if isinstance(stored, WorkspaceError):
                stored = record
            else:
                self._event("WorkspaceRecoveryRequired", context, stored, reason="root tombstone could not be confirmed")
            return self._deletion_receipt(stored, fence, EffectState.UNKNOWN, ("ROOT",), reason="root tombstone could not be confirmed")
        deleted = replace(record, state=WorkspaceState.DELETED, version=record.version + 1, deleted_at=self._now(), deletion_checkpoint=None)
        stored = self.registry.replace(deleted, expected_version=record.version)
        if isinstance(stored, WorkspaceError):
            return self._deletion_receipt(record, fence, EffectState.UNKNOWN, ("ROOT",), reason="delete commit is indeterminate")
        self._event("WorkspaceDeleted", context, stored)
        return self._deletion_receipt(stored, fence, EffectState.APPLIED, ("ROOT", "METADATA", "ARTIFACTS", "TEMPORARIES", "AUDIT"))

    def delete(self, command: DeleteWorkspace) -> WorkspaceDeletionReceipt | WorkspaceError:
        denied = self._authorize(command.context)
        if denied:
            return denied
        if command.recovery_window <= timedelta(0) or command.recovery_window > timedelta(days=30):
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        with self._lock:
            current = self.registry.get(command.context)
            if current is None:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if current.state is WorkspaceState.DELETED:
                fence = current.deletion_fence or FencingToken(1)
                return self._deletion_receipt(current, fence, EffectState.APPLIED, ("TOMBSTONE",))
            if current.version != command.expected_version:
                return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
            if current.root_descriptor is None or current.root_descriptor.root_identity != command.expected_root_identity:
                return self._error(WorkspaceErrorCode.ROOT_MISMATCH)
            if current.state is not WorkspaceState.DELETING:
                if current.state is WorkspaceState.PROVISIONING:
                    return self._error(WorkspaceErrorCode.STATE_REJECTED)
                self._fence_counter += 1
                fence = FencingToken(self._fence_counter)
                deleting = replace(current, state=WorkspaceState.DELETING, version=current.version + 1, deletion_requested_at=self._now(), deletion_fence=fence, deletion_checkpoint=None)
                stored = self.registry.replace(deleting, expected_version=current.version)
                if isinstance(stored, WorkspaceError):
                    return stored
                current = stored
                self._revoke_active_leases(current.workspace_id)
                if current.usage.active_leases:
                    usage = replace(current.usage, active_leases=0, measured_at=self._now())
                    updated = replace(current, usage=usage, version=current.version + 1)
                    stored = self.registry.replace(updated, expected_version=current.version)
                    if not isinstance(stored, WorkspaceError):
                        current = stored
                self._event("WorkspaceDeletionStarted", command.context, current, reason=command.reason)
            else:
                fence = current.deletion_fence or FencingToken(max(1, self._fence_counter))
            return self._continue_cleanup(command.context, current, fence)

    def reconcile(self, command: ReconcileWorkspace) -> WorkspaceReconciliationReceipt | WorkspaceError:
        denied = self._authorize(command.context)
        if denied:
            return denied
        if command.maximum_entries < 1 or command.maximum_entries > 10000:
            return self._error(WorkspaceErrorCode.INVALID_REQUEST)
        with self._lock:
            current = self.registry.get(command.context)
            if current is None:
                return self._error(WorkspaceErrorCode.NOT_FOUND)
            if current.version != command.expected_version:
                return self._error(WorkspaceErrorCode.VERSION_CONFLICT)
            if current.root_descriptor is None:
                return self._error(WorkspaceErrorCode.RECOVERY_REQUIRED, retryability=Retryability.AFTER_RECONCILIATION)
            scope = ReconcileScope(command.scope)
            if scope in (ReconcileScope.CLEANUP, ReconcileScope.ALL) and current.state is WorkspaceState.DELETING:
                fence = current.deletion_fence or FencingToken(1)
                deletion = self._continue_cleanup(command.context, current, fence)
                return WorkspaceReconciliationReceipt(current.workspace_id, scope, deletion.state, deletion.version, deletion.effect_state, 0, 1, ("CLEANUP_CONTINUED",), deletion.checkpoint)
            repaired = 0
            inspected = 0
            evidence: list[str] = []
            if scope in (ReconcileScope.ROOT, ReconcileScope.USAGE, ReconcileScope.ALL):
                observation = self.root_adapter.inspect(current.root_descriptor, command.maximum_entries)
                inspected = observation.entries
                if current.state is not WorkspaceState.DELETED and (observation.identity != current.root_descriptor.root_identity or observation.health.value != "READY"):
                    changed = replace(current, state=WorkspaceState.RECOVERY_REQUIRED, version=current.version + 1)
                    stored = self.registry.replace(changed, expected_version=current.version)
                    if not isinstance(stored, WorkspaceError):
                        self._event("WorkspaceRecoveryRequired", command.context, stored, reason=observation.unsafe_reason or "root reconciliation failed")
                        return WorkspaceReconciliationReceipt(stored.workspace_id, scope, stored.state, stored.version, EffectState.APPLIED, inspected, 1, ("ROOT_DIVERGENT",))
                if scope in (ReconcileScope.USAGE, ReconcileScope.ALL) and current.state is not WorkspaceState.DELETED:
                    observation = self.root_adapter.inspect(current.root_descriptor, current.quota.maximum_entries + 1)
                    state = UsageReconciliationState.CURRENT if observation.entries <= current.quota.maximum_entries and observation.bytes <= current.quota.maximum_bytes else UsageReconciliationState.DIVERGENT
                    usage = replace(current.usage, accounted_entries=observation.entries, accounted_bytes=observation.bytes, measured_at=self._now(), reconciliation_state=state)
                    updated = replace(current, usage=usage, version=current.version + 1)
                    stored = self.registry.replace(updated, expected_version=current.version)
                    if not isinstance(stored, WorkspaceError):
                        current = stored
                        repaired += 1
                        if state is UsageReconciliationState.DIVERGENT:
                            evidence.append("USAGE_DIVERGENT")
            if scope in (ReconcileScope.LEASES, ReconcileScope.ALL):
                expired = 0
                for lease_id, lease in tuple(self._leases.items()):
                    if lease.workspace_id == current.workspace_id and lease.state is LeaseState.ACTIVE and self._now() >= lease.expires_at:
                        self.root_adapter.release(lease.root_handle_ref)
                        self._leases[lease_id] = replace(lease, state=LeaseState.EXPIRED)
                        expired += 1
                if expired:
                    usage = replace(current.usage, active_leases=max(0, current.usage.active_leases - expired), measured_at=self._now())
                    updated = replace(current, usage=usage, version=current.version + 1)
                    stored = self.registry.replace(updated, expected_version=current.version)
                    if not isinstance(stored, WorkspaceError):
                        current = stored
                        repaired += expired
                evidence.append("LEASES_SCANNED")
            if not evidence:
                evidence.append("RECONCILED")
            return WorkspaceReconciliationReceipt(current.workspace_id, scope, current.state, current.version, EffectState.APPLIED, inspected, repaired, tuple(evidence))


__all__ = ["InMemoryWorkspaceEventSink", "WorkspaceManagerService"]
