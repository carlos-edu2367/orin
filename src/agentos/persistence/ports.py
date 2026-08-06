from __future__ import annotations

from typing import Protocol

from .models import (
    AuthorizedRead,
    AuthorizedRecord,
    AuthorizedRecordPage,
    AuthorizedScan,
    InspectCommit,
    NotFound,
    TransactionReceipt,
    TransactionRequest,
    TransactionResult,
)


class TransactionalPersistence(Protocol):
    def transact(self, request: TransactionRequest) -> TransactionResult: ...

    def read(self, query: AuthorizedRead) -> AuthorizedRecord | NotFound: ...

    def scan(self, query: AuthorizedScan) -> AuthorizedRecordPage: ...

    def inspect_commit(self, query: InspectCommit) -> TransactionReceipt: ...


__all__ = ["TransactionalPersistence"]
