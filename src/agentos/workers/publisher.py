"""Polling worker for the durable SQLite conversation queue.

The database owns dispatch state. This process claims it directly, so Redis and
ARQ are not part of the local product. Multiple conversations run concurrently
across worker threads while turns within the same conversation are serialized.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
import logging
import os
from pathlib import Path
import signal
import threading
import time
from typing import Any

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


_ORPHANS_SWEPT = False


def auto_resume_interrupted_agents(
    engine: Any,
    *,
    store: Any = None,
    worker: Any = None,
    runs_dir: Path | None = None,
    settings_loader: Callable[[str], Any] | None = None,
    quota_policy: Any = None,
    resume_handler: Callable[[Any], Any] | None = None,
) -> list[str]:
    """Optionally auto-resume interrupted background runs on worker boot (WS-B4, B-RF4).

    Disabled by default (``orchestration.background.autoResume = False``).
    When enabled, resumes each interrupted agent **at most once** (guarded by the
    persisted ``auto_resumed`` flag on disk to avoid crash loops) and honors
    ``QuotaPolicy.check_background``.
    """
    from sqlalchemy import select, update

    from agentos.agentic.agent_run_persistence import AgentRunRecorder
    from agentos.persistence.postgres.schema import agent_runs, conversation_agents
    from agentos.quota.policy import QuotaPolicy
    from agentos.settings.registry import resolve as resolve_setting
    from agentos.settings.loader import EffectiveSettings, SettingsLayer

    def _coerce_settings(raw: Any) -> EffectiveSettings:
        if isinstance(raw, EffectiveSettings):
            return raw
        if isinstance(raw, Mapping):
            data = dict(raw)
            return EffectiveSettings(
                data=data,
                provenance={},
                layers=(SettingsLayer(name="user", path=None, data=data),),
                errors=(),
                trusted=True,
            )
        return EffectiveSettings(data={}, provenance={}, layers=(), errors=(), trusted=True)

    def _load_settings(user_id: str) -> EffectiveSettings:
        if settings_loader is not None:
            return _coerce_settings(settings_loader(user_id))
        try:
            from agentos.settings.loader import load_effective_settings

            return load_effective_settings(user_id=user_id)
        except Exception:  # noqa: BLE001
            return _coerce_settings({})

    def _resolve_quota_policy() -> QuotaPolicy | None:
        if quota_policy is not None:
            return quota_policy
        try:
            from agentos.persistence.postgres.quota import QuotaStore
            from agentos.quota.service import QuotaService

            return QuotaPolicy(QuotaService(QuotaStore(engine)))
        except Exception:  # noqa: BLE001
            return None

    recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
    with engine.connect() as conn:
        interrupted_ids = [
            str(row[0])
            for row in conn.execute(
                select(agent_runs.c.agent_id).where(agent_runs.c.state == "interrupted")
            ).all()
            if row and row[0]
        ]
    if not interrupted_ids:
        return []

    policy = _resolve_quota_policy()
    settings_cache: dict[str, EffectiveSettings] = {}
    resumed: list[str] = []

    for agent_id in interrupted_ids:
        run = recorder.get(agent_id)
        if run is None or run.state != "interrupted":
            continue
        effective = settings_cache.get(run.user_id)
        if effective is None:
            effective = _load_settings(run.user_id)
            settings_cache[run.user_id] = effective

        if not bool(resolve_setting(effective, "orchestration.background.autoResume").value):
            continue

        if recorder.has_auto_resumed(agent_id):
            continue

        if policy is not None and run.provider:
            decision = policy.check_background(
                run.user_id,
                run.provider,
                model_id=run.model_id,
                settings=effective,
            )
            if not decision.allowed:
                _LOGGER.info(
                    "auto-resume skipped for %s due to quota policy (%s)",
                    agent_id,
                    decision.reason,
                )
                continue

        now = datetime.now(UTC)
        transitioned = recorder.store.transition_state(
            agent_id,
            from_state="interrupted",
            to_state="queued",
            last_activity_at=now,
        )
        if transitioned is None:
            continue
        # Persist auto_resumed BEFORE dispatching so a sudden crash cannot loop,
        # and roll back both state and flag if dispatch raises synchronously (RT-B-05).
        recorder.mark_auto_resumed(agent_id)
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(conversation_agents)
                    .where(conversation_agents.c.agent_id == agent_id)
                    .values(state="working", updated_at=now)
                )
        except Exception:  # noqa: BLE001
            pass

        try:
            if resume_handler is not None:
                resume_handler(transitioned)
            elif worker is not None and callable(getattr(worker, "resume_subagent", None)):
                if not worker.resume_subagent(agent_id, runs_dir=runs_dir):
                    raise RuntimeError(f"worker.resume_subagent returned False for {agent_id}")
            elif store is not None:
                # Leave durably in 'queued' for ChatWorker.resume_queued_subagents without creating fake user messages.
                pass
        except Exception:  # noqa: BLE001
            _LOGGER.warning("auto-resume dispatch failed for %s; reverting to interrupted", agent_id, exc_info=True)
            rollback_ts = datetime.now(UTC)
            recorder.store.touch(agent_id, state="interrupted", last_activity_at=rollback_ts)
            try:
                recorder.clear_auto_resumed(agent_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                with engine.begin() as conn:
                    conn.execute(
                        update(conversation_agents)
                        .where(conversation_agents.c.agent_id == agent_id)
                        .values(state="interrupted", updated_at=rollback_ts)
                    )
            except Exception:  # noqa: BLE001
                pass
            continue

        resumed.append(agent_id)

    return resumed


def recover_once(store, *, has_in_flight: bool = False, worker: Any = None) -> tuple[str, ...]:
    """Requeue turns after a worker crash and fail turns no worker can claim."""
    global _ORPHANS_SWEPT
    if not has_in_flight and not _ORPHANS_SWEPT:
        try:
            from agentos.code_mode.workspace_lock import sweep_stale_workspace_locks

            sweep_stale_workspace_locks()
        except Exception:
            _LOGGER.debug("could not sweep stale workspace locks", exc_info=True)
        engine = getattr(store, "_engine", None)
        if engine is not None:
            try:
                from agentos.agentic.background_agents import fail_orphaned_running_agents

                fail_orphaned_running_agents(engine)
                auto_resume_interrupted_agents(engine, store=store, worker=worker)
                _ORPHANS_SWEPT = True
            except Exception:
                _LOGGER.debug("could not sweep orphaned running agents", exc_info=True)
        else:
            _ORPHANS_SWEPT = True
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


def _heartbeat_worker(worker: ChatWorker) -> None:
    refresh = getattr(worker, "_refresh_heartbeat_if_due", None)
    if callable(refresh):
        refresh()
    else:
        worker.store.heartbeat("chat-worker")


def poll_once(worker_or_runner: ChatWorker | ConcurrentTurnRunner, next_sweep: float) -> tuple[bool, float]:
    """Run one polling tick: claim pending turns before running watchdog sweeps."""
    has_in_flight = False
    if isinstance(worker_or_runner, ConcurrentTurnRunner):
        runner = worker_or_runner
        worker = runner.worker
        _heartbeat_worker(worker)
        submitted = runner.submit_pending()
        has_in_flight = runner.active_count() > 0
        if submitted:
            return True, next_sweep
    else:
        worker = worker_or_runner
        _heartbeat_worker(worker)
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
        recover_once(worker.store, has_in_flight=has_in_flight, worker=worker)
        try:
            resume_queued = getattr(worker, "resume_queued_subagents", None)
            if callable(resume_queued):
                resume_queued()
        except Exception:
            _LOGGER.exception("could not drain queued subagent resumptions")
        try:
            worker.sweep_exited_processes()
        except Exception:
            _LOGGER.exception("could not sweep exited background processes")
    return False, next_sweep


def install_shutdown_handlers() -> None:
    """Turn the supervisor's stop signal into SystemExit so that main()'s ``finally`` runs (SessionEnd).

    Python's default for SIGTERM and SIGBREAK ends the process without unwinding, so the launcher's stop never
    reached the cleanup. Only the first signal unwinds: the launcher escalates to terminate() after its grace
    period, and a second signal must not cut the shutdown short. Off the main thread this does nothing, because
    the signal module only accepts handlers there.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    stopping = threading.Event()

    def on_stop(signum: int, frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        raise SystemExit(0)

    for name in ("SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, on_stop)
        except (OSError, ValueError):
            _LOGGER.warning("could not install the %s shutdown handler", name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner: ConcurrentTurnRunner | None = None
    try:
        install_shutdown_handlers()
        worker = create_chat_worker()
        runner = ConcurrentTurnRunner(worker)
        next_sweep = 0.0
        while True:
            try:
                ran_turn, next_sweep = poll_once(runner, next_sweep)
                if ran_turn:
                    continue
            except Exception:
                _LOGGER.exception("worker tick failed")
            time.sleep(POLL_SECONDS)
    finally:
        # SessionEnd first, with its own budget: it must fit the launcher's grace period even when turns are in flight.
        try:
            from agentos.hooks.session_end import end_sessions

            end_sessions("other", timeout_seconds=5.0)
        except Exception:
            _LOGGER.exception("SessionEnd hooks could not run at shutdown")
        if runner is not None:
            runner.shutdown(wait=True)
        try:
            from agentos.agentic.tools._shared import cleanup_session_background_processes

            cleanup_session_background_processes()
        except Exception:
            _LOGGER.debug("could not cleanup session background processes", exc_info=True)


if __name__ == "__main__":
    main()
