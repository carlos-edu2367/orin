from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from threading import RLock
from typing import Protocol

from .models import (
    CapabilityDescriptor,
    CapabilityOperationContext,
    CapabilityProgram,
    CapabilityRef,
    CapabilityRegistryOperationContext,
    AuthorizedCapabilityRegistryQuery,
    CapabilityStatus,
    DisableCapability,
    RegisterCapability,
)


class RegistryNotFound(LookupError):
    """The exact capability version is not visible in the caller scope."""


class RegistryConflict(ValueError):
    """An immutable version or scoped idempotency record conflicts."""


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    descriptor: CapabilityDescriptor
    program_ref: str
    already_applied: bool = False


class CapabilityRegistry(Protocol):
    def register(self, request: RegisterCapability) -> RegistrationResult: ...

    def resolve(self, capability_ref: CapabilityRef, context: CapabilityOperationContext) -> CapabilityDescriptor: ...

    def program(self, capability_ref: CapabilityRef, context: CapabilityOperationContext) -> CapabilityProgram: ...

    def list(self, context: CapabilityRegistryOperationContext, *, status: tuple[CapabilityStatus, ...] = ()) -> tuple[CapabilityDescriptor, ...]: ...

    def disable(self, request: DisableCapability) -> RegistrationResult: ...


@dataclass(frozen=True, slots=True)
class _Entry:
    descriptor: CapabilityDescriptor
    program: CapabilityProgram
    program_ref: str
    integrity_ref: str
    owner_user_id: str
    owner_workspace_id: str | None


class InMemoryCapabilityRegistry:
    """Deterministic reference registry; execution code depends only on the protocol."""

    def __init__(self) -> None:
        self._entries: dict[CapabilityRef, _Entry] = {}
        self._idempotency: dict[tuple[tuple[str, ...], str], tuple[str, RegistrationResult]] = {}
        self._lock = RLock()
        self._bootstrap_closed = False

    def register(self, request: RegisterCapability) -> RegistrationResult:
        with self._lock:
            self._validate_admin(request.context, bootstrap=request.context.purpose == "SYSTEM_BOOTSTRAP")
            ref = request.descriptor.capability_ref
            fingerprint = self._fingerprint(request)
            key = (self._context_key(request.context), str(request.idempotency_key))
            prior = self._idempotency.get(key)
            if prior is not None:
                if prior[0] != fingerprint:
                    raise RegistryConflict("idempotency key conflicts with prior registration")
                return replace(prior[1], already_applied=True)
            if request.context.purpose == "SYSTEM_BOOTSTRAP":
                if self._entries or self._bootstrap_closed:
                    raise PermissionError("bootstrap is closed after the initial catalog transaction")
            elif not request.context.actor:
                raise PermissionError("registry actor is required")
            if ref in self._entries:
                raise RegistryConflict("published capability version is immutable")
            program_ref = f"program:{ref.capability_id}:{ref.version}"
            result = RegistrationResult(request.descriptor, program_ref)
            self._entries[ref] = _Entry(
                descriptor=request.descriptor,
                program=request.program,
                program_ref=program_ref,
                integrity_ref=str(request.package_integrity_ref),
                owner_user_id=str(request.context.user_id),
                owner_workspace_id=str(request.context.workspace_id) if request.context.workspace_id is not None else None,
            )
            self._idempotency[key] = (fingerprint, result)
            if request.context.purpose == "SYSTEM_BOOTSTRAP":
                self._bootstrap_closed = True
            return result

    def resolve(self, capability_ref: CapabilityRef, context: CapabilityOperationContext) -> CapabilityDescriptor:
        with self._lock:
            self._validate_execution_context(context)
            entry = self._entries.get(capability_ref)
            if entry is None:
                raise RegistryNotFound("capability version is not registered")
            if entry.owner_user_id != str(context.user_id) or entry.owner_workspace_id != (str(context.workspace_id) if context.workspace_id is not None else None):
                raise PermissionError("capability is outside the caller ownership scope")
            return entry.descriptor

    def program(self, capability_ref: CapabilityRef, context: CapabilityOperationContext) -> CapabilityProgram:
        with self._lock:
            self.resolve(capability_ref, context)
            return self._entries[capability_ref].program

    def list(
        self,
        context: CapabilityRegistryOperationContext | AuthorizedCapabilityRegistryQuery,
        *,
        status: tuple[CapabilityStatus, ...] = (),
    ) -> tuple[CapabilityDescriptor, ...]:
        with self._lock:
            query = context if isinstance(context, AuthorizedCapabilityRegistryQuery) else None
            if query is not None:
                context = query.context
                status = query.status
            self._validate_admin(context, bootstrap=False)
            statuses = tuple(CapabilityStatus(item) for item in status)
            return tuple(
                entry.descriptor
                for ref, entry in sorted(self._entries.items(), key=lambda item: (str(item[0].capability_id), int(item[0].version)))
                if (not statuses or entry.descriptor.status in statuses)
                and (query is None or query.capability_ref is None or ref == query.capability_ref)
                and (query is None or all(permission in entry.descriptor.permissions for permission in query.permission_filter))
            )

    def disable(self, request: DisableCapability) -> RegistrationResult:
        with self._lock:
            self._validate_admin(request.context, bootstrap=False)
            entry = self._entries.get(request.capability_ref)
            if entry is None:
                raise RegistryNotFound("capability version is not registered")
            fingerprint = self._fingerprint(request)
            key = (self._context_key(request.context), str(request.idempotency_key))
            prior = self._idempotency.get(key)
            if prior is not None:
                if prior[0] != fingerprint:
                    raise RegistryConflict("idempotency key conflicts with prior disable")
                return replace(prior[1], already_applied=True)
            if entry.descriptor.status is not request.expected_status:
                raise RegistryConflict("expected capability status is stale")
            descriptor = replace(entry.descriptor, status=CapabilityStatus.DISABLED)
            self._entries[request.capability_ref] = replace(entry, descriptor=descriptor)
            result = RegistrationResult(descriptor, entry.program_ref)
            self._idempotency[key] = (fingerprint, result)
            return result

    @staticmethod
    def _context_key(context: CapabilityRegistryOperationContext) -> tuple[str, ...]:
        return (
            str(context.user_id), str(context.workspace_id or ""), str(context.agent_id or ""),
            str(context.execution_id or ""), str(context.administrative_correlation_id or ""),
            str(context.correlation_id), str(context.actor),
        )

    @staticmethod
    def _validate_execution_context(context: CapabilityOperationContext) -> None:
        if not context.execution_id:
            raise PermissionError("execution context is required")

    @staticmethod
    def _validate_admin(context: CapabilityRegistryOperationContext, *, bootstrap: bool) -> None:
        if not context.user_id or not context.correlation_id or not context.actor:
            raise PermissionError("complete administrative context is required")
        if bootstrap and context.purpose != "SYSTEM_BOOTSTRAP":
            raise PermissionError("bootstrap purpose is exact")

    @staticmethod
    def _fingerprint(request: object) -> str:
        return sha256(repr(request).encode("utf-8")).hexdigest()


__all__ = ["CapabilityRegistry", "InMemoryCapabilityRegistry", "RegistrationResult", "RegistryConflict", "RegistryNotFound"]
