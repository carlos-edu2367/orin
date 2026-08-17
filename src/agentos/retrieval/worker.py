"""Indexing off the turn's hot path.

A daemon thread with one queue, deliberately not the durable dispatch in
``workers/``. That machinery exists for work that must survive a crash and be
leased exactly once; a reconstructible index needs neither. If this process
dies mid-scan, the next scan simply redoes the work.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Protocol


logger = logging.getLogger(__name__)

QUEUE_LIMIT = 256


class Reindexable(Protocol):
    def reindex(self, paths: list[str] | None = None) -> None: ...


class IndexWorker:
    def __init__(self, service: Reindexable) -> None:
        self._service = service
        self._queue: queue.Queue[list[str] | None] = queue.Queue(maxsize=QUEUE_LIMIT)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="orin-retrieval-index", daemon=True)
        self._thread.start()

    def enqueue_full_scan(self) -> None:
        self._offer(None)

    def enqueue_paths(self, paths: list[str]) -> None:
        bounded = [path for path in paths if isinstance(path, str) and path.strip()]
        if bounded:
            self._offer(bounded)

    def _offer(self, item: list[str] | None) -> None:
        """Drop the request rather than block a turn when the queue is saturated."""
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.debug("retrieval index queue is full; dropping a reindex request")

    def wait_idle(self, *, timeout: float = 5.0) -> bool:
        """Block until the queue drains. Used by tests and by shutdown."""
        deadline = threading.Event()
        waiter = threading.Thread(target=lambda: (self._queue.join(), deadline.set()), daemon=True)
        waiter.start()
        return deadline.wait(timeout)

    def stop(self) -> None:
        self._stopping.set()
        self._offer(None)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if not self._stopping.is_set():
                    self._service.reindex(item)
            except Exception:
                # A failed scan must never take the thread down: the index would
                # then stop updating silently for the rest of the session.
                logger.exception("retrieval reindex failed")
            finally:
                self._queue.task_done()


__all__ = ["IndexWorker", "QUEUE_LIMIT", "Reindexable"]
