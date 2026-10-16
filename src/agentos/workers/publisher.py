"""Polling worker for the durable SQLite conversation queue.

The database owns dispatch state. This process claims it directly, so Redis and
ARQ are not part of the local product. One worker process is intentional:
SQLite has a single writer and turns may run for a long time.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from agentos.workers.chat import ChatWorker, create_chat_worker


_LOGGER = logging.getLogger("agentos.workers.worker")
POLL_SECONDS = 0.35
STALE_AFTER = timedelta(seconds=600)
UNCLAIMED_AFTER = timedelta(seconds=45)
SWEEP_EVERY_SECONDS = 15.0


def recover_once(store) -> tuple[str, ...]:
    """Requeue turns after a worker crash and fail turns no worker can claim."""
    recovered = ChatWorker(store).watchdog(maximum_age=UNCLAIMED_AFTER)
    stale = ChatWorker(store).recover_stale(maximum_age=STALE_AFTER)
    if recovered or stale:
        _LOGGER.info("recovery swept %d unclaimed and %d stale turns", len(recovered), len(stale))
    return tuple({*recovered, *stale})


def poll_once(worker: ChatWorker, next_sweep: float) -> tuple[bool, float]:
    """Run one polling tick: claim pending turns before running watchdog sweeps."""
    worker.store.heartbeat("chat-worker")
    pending = worker.store.pending()
    if pending:
        worker.run(str(pending[0]))
        return True, next_sweep
    now_mono = time.monotonic()
    if now_mono >= next_sweep:
        worker.store.heartbeat("chat-worker")
        next_sweep = now_mono + SWEEP_EVERY_SECONDS
        recover_once(worker.store)
    return False, next_sweep


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = create_chat_worker()
    next_sweep = 0.0
    while True:
        try:
            ran_turn, next_sweep = poll_once(worker, next_sweep)
            if ran_turn:
                continue
        except Exception:
            _LOGGER.exception("worker tick failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
