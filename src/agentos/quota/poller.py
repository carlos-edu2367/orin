"""Background daemon poller that refreshes passive quota files while SSE clients are connected."""

from __future__ import annotations

import logging
import threading
from typing import Protocol

logger = logging.getLogger("agentos.quota.poller")

DEFAULT_POLL_INTERVAL_SECONDS = 60.0


class QuotaRefreshable(Protocol):
    def refresh_from_files(self, user_id: str) -> object: ...


class QuotaPoller:
    """Refreshes local quota files every 60s only when at least one SSE client is connected."""

    def __init__(
        self,
        service: QuotaRefreshable,
        *,
        interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._service = service
        self._interval = max(0.01, float(interval_seconds))
        self._lock = threading.Lock()
        self._client_counts: dict[str, int] = {}
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
            else:
                self._client_counts[uid] = remaining

    def trigger_after_turn(self, user_id: str) -> None:
        """Run an immediate passive refresh after a CLI turn finishes."""
        uid = str(user_id)
        if not uid:
            return
        try:
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

    def _run(self) -> None:
        while not self._stop_event.is_set():
            users = self._active_users()
            if not users:
                # Sleep until a client connects or stop() is called
                self._wake_event.wait(timeout=self._interval)
                self._wake_event.clear()
                continue

            for uid in users:
                if self._stop_event.is_set():
                    return
                try:
                    self._service.refresh_from_files(uid)
                except Exception:  # noqa: BLE001
                    logger.debug("quota poll failed for user %s", uid, exc_info=True)

            self._stop_event.wait(timeout=self._interval)


__all__ = ["DEFAULT_POLL_INTERVAL_SECONDS", "QuotaPoller", "QuotaRefreshable"]
