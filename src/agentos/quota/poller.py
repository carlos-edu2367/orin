"""Background daemon poller: passive files every 60 s and active CLI probes every N minutes while SSE clients are connected."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger("agentos.quota.poller")

DEFAULT_POLL_INTERVAL_SECONDS = 60.0
DEFAULT_ACTIVE_INTERVAL_SECONDS = 5 * 60.0


class QuotaRefreshable(Protocol):
    def refresh_from_files(self, user_id: str) -> object: ...


class QuotaPoller:
    """Rereads local quota files every 60 s, and runs the active probes every ``quota.activeProbe.intervalMinutes``,
    only while at least one SSE client is connected."""

    def __init__(
        self,
        service: QuotaRefreshable,
        *,
        interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._interval = max(0.01, float(interval_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._client_counts: dict[str, int] = {}
        self._last_active: dict[str, float] = {}
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active_client_count(self) -> int:
        with self._lock:
            return sum(self._client_counts.values())

    def client_connected(self, user_id: str) -> None:
        uid = str(user_id)
        if not uid:
            return
        with self._lock:
            self._client_counts[uid] = self._client_counts.get(uid, 0) + 1
        self._wake_event.set()

    def client_disconnected(self, user_id: str) -> None:
        uid = str(user_id)
        if not uid:
            return
        with self._lock:
            remaining = self._client_counts.get(uid, 0) - 1
            if remaining <= 0:
                self._client_counts.pop(uid, None)
                # The next connection counts as "opened the app": probe right away (still throttled to 60 s).
                self._last_active.pop(uid, None)
            else:
                self._client_counts[uid] = remaining

    def trigger_after_turn(self, user_id: str, provider: str | None = None) -> None:
        """After a turn: reread local files and, when ``provider`` is known, probe only that subscription."""
        uid = str(user_id)
        if not uid:
            return
        try:
            after_turn = getattr(self._service, "after_turn", None)
            if callable(after_turn):
                after_turn(uid, provider)
            else:
                self._service.refresh_from_files(uid)
        except Exception:  # noqa: BLE001
            logger.debug("on-demand quota refresh failed for user %s", uid, exc_info=True)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="orin-quota-poller",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _active_users(self) -> list[str]:
        with self._lock:
            return [uid for uid, count in self._client_counts.items() if count > 0]

    def _active_probe_due(self, uid: str) -> bool:
        settings_for = getattr(self._service, "probe_settings", None)
        refresh_active = getattr(self._service, "refresh_active", None)
        if not callable(settings_for) or not callable(refresh_active):
            return False
        try:
            settings = settings_for(uid)
        except Exception:  # noqa: BLE001
            return False
        if not getattr(settings, "enabled", False):
            return False
        now = self._clock()
        with self._lock:
            last = self._last_active.get(uid)
            if last is not None and now - last < float(settings.interval_seconds):
                return False
            self._last_active[uid] = now
        return True

    def poll_once(self) -> None:
        """One tick for every connected user: passive read always, active probes when their interval elapsed."""
        for uid in self._active_users():
            if self._stop_event.is_set():
                return
            try:
                self._service.refresh_from_files(uid)
            except Exception:  # noqa: BLE001
                logger.debug("quota poll failed for user %s", uid, exc_info=True)
            try:
                if self._active_probe_due(uid):
                    self._service.refresh_active(uid, reason="poller")  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                logger.debug("active quota probe failed for user %s", uid, exc_info=True)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self._active_users():
                # Sleep until a client connects or stop() is called
                self._wake_event.wait(timeout=self._interval)
                self._wake_event.clear()
                continue

            self.poll_once()
            self._stop_event.wait(timeout=self._interval)


__all__ = [
    "DEFAULT_ACTIVE_INTERVAL_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "QuotaPoller",
    "QuotaRefreshable",
]
