"""Redis/ARQ boundary. It intentionally transports only durable identifiers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from inspect import isawaitable

from .models import WorkItem


@dataclass(frozen=True, slots=True)
class ArqQueueReceipt:
    job_id: str


class RedisArqWorkQueue:
    def __init__(self, arq_pool, *, namespace: str) -> None:
        if not namespace or not namespace.strip():
            raise ValueError("namespace must be non-blank")
        if not hasattr(arq_pool, "enqueue_job"):
            raise TypeError("arq_pool must provide enqueue_job")
        self._arq_pool = arq_pool
        self._namespace = namespace

    def enqueue(self, item: WorkItem) -> ArqQueueReceipt:
        result = self._arq_pool.enqueue_job(
            f"agentos:{item.pool.value}",
            work_item_id=item.work_item_id,
            dispatch_attempt_id=item.dispatch_attempt_id,
        )
        if isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                result = asyncio.run(result)
            else:
                raise RuntimeError("use enqueue_async from an asyncio event loop")
        return ArqQueueReceipt(str(result))

    async def enqueue_async(self, item: WorkItem) -> ArqQueueReceipt:
        result = self._arq_pool.enqueue_job(
            f"agentos:{item.pool.value}", work_item_id=item.work_item_id, dispatch_attempt_id=item.dispatch_attempt_id
        )
        if isawaitable(result):
            result = await result
        return ArqQueueReceipt(str(result))


__all__ = ["ArqQueueReceipt", "RedisArqWorkQueue"]
