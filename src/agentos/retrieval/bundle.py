"""The service the tools call, plus the thread that keeps its index fresh."""
from __future__ import annotations

from dataclasses import dataclass

from .service import RetrievalService
from .worker import IndexWorker


@dataclass(frozen=True, slots=True)
class RetrievalBundle:
    service: RetrievalService
    worker: IndexWorker

    def close(self) -> None:
        """Stop the background thread, then close its sqlite connection.

        Order matters: stopping the worker first means no new scan can start
        once the store is closed underneath it.
        """
        self.worker.stop()
        self.service.close()


__all__ = ["RetrievalBundle"]
