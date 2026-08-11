from __future__ import annotations

from typing import Protocol

from .models import DispatchAttempt, WorkItem


class WorkQueue(Protocol):
    def enqueue(self, item: WorkItem) -> "QueueReceipt": ...


class DispatchStore(Protocol):
    def create(self, item: WorkItem) -> DispatchAttempt: ...
    def lease(self, dispatch_attempt_id: str, *, worker_id: str, lease_id: str, fence: int, expected_version: int) -> DispatchAttempt: ...
    def acknowledge(self, dispatch_attempt_id: str, *, lease_id: str, fence: int, expected_version: int) -> DispatchAttempt: ...


class QueueReceipt(Protocol):
    job_id: str
