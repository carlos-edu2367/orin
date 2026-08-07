from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    AuthorizedMemory,
    ApplyMemoryRetention,
    ConsolidateMemory,
    GetMemory,
    InvalidateMemory,
    MemoryCommitRequest,
    MemoryCommitResult,
    MemoryMatch,
    MemoryOperationContext,
    MemoryRecord,
    MemorySearchResult,
    SearchMemory,
    SaveMemory,
)


class MemoryManager(Protocol):
    def save(self, command: SaveMemory): ...

    def get(self, query: GetMemory) -> AuthorizedMemory: ...

    def search(self, query: SearchMemory) -> MemorySearchResult: ...

    def invalidate(self, command: InvalidateMemory): ...

    def consolidate(self, command: ConsolidateMemory): ...

    def apply_retention(self, command: ApplyMemoryRetention): ...


class MemorySearchAdapter(Protocol):
    def rank(self, records: tuple[MemoryRecord, ...], query: SearchMemory) -> tuple[MemoryMatch, ...]: ...


class MemoryStore(Protocol):
    def get(self, memory_id: str) -> MemoryRecord | None: ...

    def list_records(self) -> tuple[MemoryRecord, ...]: ...

    def next_event_sequence(self, execution_id: str) -> int: ...

    def lookup_idempotency(self, context: MemoryOperationContext, operation: str, key: str, fingerprint: str): ...

    def commit(self, request: MemoryCommitRequest) -> MemoryCommitResult: ...


class MemoryAuthorizationPolicy(Protocol):
    def authorize(
        self,
        context: MemoryOperationContext,
        record: MemoryRecord | None,
        *,
        operation: str,
        reference=None,
        classification_ceiling=None,
        grant_refs: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> None: ...


class MemoryClock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "MemoryAuthorizationPolicy",
    "MemoryClock",
    "MemoryManager",
    "MemorySearchAdapter",
    "MemoryStore",
]
