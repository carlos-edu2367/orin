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
    MemoryConsolidationReceipt,
    MemoryMatch,
    MemoryOperationContext,
    MemoryRecord,
    MemorySearchResult,
    MemorySearchCapabilities,
    MemoryWriteReceipt,
    RetentionReceipt,
    SearchMemory,
    SaveMemory,
)


class MemoryManager(Protocol):
    def save(self, command: SaveMemory) -> MemoryWriteReceipt: ...

    def get(self, query: GetMemory) -> AuthorizedMemory: ...

    def search(self, query: SearchMemory) -> MemorySearchResult: ...

    def invalidate(self, command: InvalidateMemory) -> MemoryWriteReceipt: ...

    def consolidate(self, command: ConsolidateMemory) -> MemoryConsolidationReceipt: ...

    def apply_retention(self, command: ApplyMemoryRetention) -> RetentionReceipt: ...


class MemorySearchAdapter(Protocol):
    @property
    def capabilities(self) -> MemorySearchCapabilities: ...

    def rank(self, records: tuple[MemoryRecord, ...], query: SearchMemory) -> tuple[MemoryMatch, ...]: ...


class MemoryStore(Protocol):
    def get(self, memory_id: str) -> MemoryRecord | None: ...

    def list_records(self) -> tuple[MemoryRecord, ...]: ...

    def next_event_sequence(self, execution_id: str) -> int: ...

    def lookup_idempotency(self, context: MemoryOperationContext, operation: str, key: str, fingerprint: str): ...

    def commit(self, request: MemoryCommitRequest) -> MemoryCommitResult: ...


class MemoryTransactionalCommitPort(Protocol):
    """Narrow composition boundary for RFC 601 transactional authority."""

    def commit(self, request: MemoryCommitRequest) -> MemoryCommitResult: ...

    def inspect_commit(self, transaction_id: str, idempotency_key: str) -> MemoryCommitResult: ...


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
        consume_grant: bool = True,
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
    "MemoryTransactionalCommitPort",
]
