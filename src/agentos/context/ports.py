from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    AuthorizedContextQuery,
    ContextAssemblyRequest,
    ContextCandidate,
    ContextDisposition,
    ContextManifest,
    ContextManifestReference,
    ContextOperationContext,
    ContextPolicySnapshot,
    ContextSnapshot,
    ContextTurnUpdate,
    ExecutionId,
    OwnershipScope,
)


class ContextSource(Protocol):
    source_kind: str

    def collect(self, query: AuthorizedContextQuery) -> tuple[ContextCandidate, ...]: ...


class ContextManifestRecorder(Protocol):
    def record(self, manifest: ContextManifest) -> ContextManifestReference: ...

    def load(self, reference: ContextManifestReference, ownership: OwnershipScope) -> ContextManifest: ...

    def finalize(self, execution_id: ExecutionId, disposition: ContextDisposition) -> None: ...


class ContextPolicy(Protocol):
    def resolve(self, request: ContextAssemblyRequest) -> ContextPolicySnapshot: ...


class ContextClock(Protocol):
    def now(self) -> datetime: ...


class CancellationSignal(Protocol):
    def is_cancelled(self) -> bool: ...


class ContextManager(Protocol):
    def assemble(self, request: ContextAssemblyRequest) -> ContextSnapshot: ...

    def apply_turn(self, request: ContextTurnUpdate) -> ContextSnapshot: ...

    def finalize(self, execution_id: ExecutionId, disposition: ContextDisposition) -> None: ...


__all__ = [
    "CancellationSignal",
    "ContextClock",
    "ContextManager",
    "ContextManifestRecorder",
    "ContextPolicy",
    "ContextSource",
]
