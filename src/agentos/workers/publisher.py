"""Polling worker for the durable SQLite conversation queue.

The database owns dispatch state. This process claims it directly, so Redis and
ARQ are not part of the local product. Multiple conversations run concurrently
across worker threads while turns within the same conversation are serialized.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import timedelta
import logging
import os
import threading
import time

from agentos.workers.chat import ChatWorker, create_chat_worker


_LOGGER = logging.getLogger("agentos.workers.worker")
POLL_SECONDS = 0.35
STALE_AFTER = timedelta(seconds=600)
UNCLAIMED_AFTER = timedelta(seconds=45)
SWEEP_EVERY_SECONDS = 15.0
MAX_CONCURRENT_TURNS = max(1, int(os.environ.get("ORIN_MAX_CONCURRENT_TURNS", "8") or "8"))


class ConcurrentTurnRunner:
    """Executes turns from distinct conversations concurrently up to a bounded pool size."""

    def __init__(self, worker: ChatWorker, *, max_workers: int = MAX_CONCURRENT_TURNS) -> None:
        self.worker = worker
        self.store = worker.store
        self._max_workers = max(1, int(max_workers))
        self._pool = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="orin-turn")
        self._lock = threading.Lock()
        self._in_flight: dict[str, Future[None]] = {}

    def _run_turn(self, turn_id: str) -> None:
        try:
            self.worker.run(turn_id)
        except Exception:
            _LOGGER.exception("concurrent turn %s failed in worker pool", turn_id)

    def reap(self) -> None:
        with self._lock:
            done_ids = [tid for tid, fut in self._in_flight.items() if fut.done()]
            for tid in done_ids:
                fut = self._in_flight.pop(tid)
                try:
                    fut.result()
                except Exception:
                    _LOGGER.exception("reaped turn %s raised an unhandled exception", tid)

    def available_slots(self) -> int:
        self.reap()
        with self._lock:
            return max(0, self._max_workers - len(self._in_flight))

    def active_count(self) -> int:
        self.reap()
        with self._lock:
            return len(self._in_flight)

    def in_flight_ids(self) -> tuple[str, ...]:
        self.reap()
        with self._lock:
            return tuple(self._in_flight)

    def submit_pending(self) -> tuple[str, ...]:
        slots = self.available_slots()
        if slots <= 0:
            return ()
        pending = self.worker.store.pending()
        if not pending:
            return ()
        submitted: list[str] = []
        with self._lock:
            free = max(0, self._max_workers - len(self._in_flight))
            for raw_tid in pending:
                if len(submitted) >= free:
                    break
                turn_id = str(raw_tid)
                if turn_id in self._in_flight:
                    continue
                self._in_flight[turn_id] = self._pool.submit(self._run_turn, turn_id)
                submitted.append(turn_id)
        return tuple(submitted)

    def wait_all(self, *, timeout: float | None = None) -> None:
        with self._lock:
            futures = list(self._in_flight.values())
        if futures:
            wait(futures, timeout=timeout)
        self.reap()

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
        self.reap()


def recover_once(store, *, has_in_flight: bool = False) -> tuple[str, ...]:
    """Requeue turns after a worker crash and fail turns no worker can claim."""
    recovered = () if has_in_flight else ChatWorker(store).watchdog(maximum_age=UNCLAIMED_AFTER)
    stale = ChatWorker(store).recover_stale(maximum_age=STALE_AFTER)
    if recovered or stale:
        _LOGGER.info("recovery swept %d unclaimed and %d stale turns", len(recovered), len(stale))
    return tuple({*recovered, *stale})


def renew_leases(runner: ConcurrentTurnRunner) -> None:
    """Renew the dispatch lease of every turn this process is still running."""
    renew = getattr(runner.worker.store, "renew_dispatch_leases", None)
    if not callable(renew):
        return
    ids = runner.in_flight_ids()
    if not ids:
        return
    try:
        renew(ids)
    except Exception:
        _LOGGER.exception("could not renew the leases of %d running turns", len(ids))


def poll_once(worker_or_runner: ChatWorker | ConcurrentTurnRunner, next_sweep: float) -> tuple[bool, float]:
    """Run one polling tick: claim pending turns before running watchdog sweeps."""
    has_in_flight = False
    if isinstance(worker_or_runner, ConcurrentTurnRunner):
        runner = worker_or_runner
        worker = runner.worker
        worker.store.heartbeat("chat-worker")
        submitted = runner.submit_pending()
        has_in_flight = runner.active_count() > 0
        if submitted:
            return True, next_sweep
    else:
        worker = worker_or_runner
        worker.store.heartbeat("chat-worker")
        pending = worker.store.pending()
        if pending:
            if len(pending) == 1:
                worker.run(str(pending[0]))
            else:
                temp_runner = ConcurrentTurnRunner(worker, max_workers=min(MAX_CONCURRENT_TURNS, len(pending)))
                try:
                    temp_runner.submit_pending()
                    temp_runner.wait_all()
                finally:
                    temp_runner.shutdown(wait=True)
            return True, next_sweep
    now_mono = time.monotonic()
    if now_mono >= next_sweep:
        worker.store.heartbeat("chat-worker")
        next_sweep = now_mono + SWEEP_EVERY_SECONDS
        if isinstance(worker_or_runner, ConcurrentTurnRunner):
            renew_leases(worker_or_runner)
        recover_once(worker.store, has_in_flight=has_in_flight)
    return False, next_sweep


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = create_chat_worker()
    runner = ConcurrentTurnRunner(worker)
    next_sweep = 0.0
    try:
        while True:
            try:
                ran_turn, next_sweep = poll_once(runner, next_sweep)
                if ran_turn:
                    continue
            except Exception:
                _LOGGER.exception("worker tick failed")
            time.sleep(POLL_SECONDS)
    finally:
        runner.shutdown(wait=True)
        try:
            from agentos.hooks.session_end import end_sessions

            end_sessions("other")
        except Exception:
            _LOGGER.exception("SessionEnd hooks could not run at shutdown")


if __name__ == "__main__":
    main()
