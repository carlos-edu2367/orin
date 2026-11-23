"""Throttle, per-subscription lock and background execution for the active quota probes."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .active_probe import ACTIVE_SOURCES, ActiveProbe
from .models import QuotaSnapshot
from .normalize import normalize_provider_name

logger = logging.getLogger("agentos.quota.probe")

MIN_PROBE_INTERVAL_SECONDS = 60.0

OUTCOME_RAN = "ran"
OUTCOME_THROTTLED = "throttled"
OUTCOME_BUSY = "busy"
OUTCOME_DISABLED = "disabled"
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOME_NO_PROBE = "no_probe"
OUTCOME_EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class ProbeSettings:
    enabled: bool = True
    interval_minutes: int = 5

    @property
    def interval_seconds(self) -> float:
        return float(max(2, min(60, int(self.interval_minutes)))) * 60.0


def _spawn_thread(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="orin-quota-probe", daemon=True).start()


class ProbeCoordinator:
    """Runs active probes at most once per ``min_interval`` per (user, subscription) and one at a time per subscription.

    ``record`` persists and publishes the snapshots (``QuotaService.record_snapshots``). ``recent`` optionally lets
    a probe run in another process (API vs. worker) be seen through the store, so both honour the same interval.
    """

    def __init__(
        self,
        probes: Sequence[ActiveProbe],
        *,
        record: Callable[[str, Sequence[QuotaSnapshot]], object],
        settings_reader: Callable[[str], ProbeSettings] | None = None,
        recent: Callable[[str, str], float | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = MIN_PROBE_INTERVAL_SECONDS,
        spawn: Callable[[Callable[[], None]], Any] = _spawn_thread,
    ) -> None:
        self._probes = {normalize_provider_name(probe.provider): probe for probe in probes}
        self._record = record
        self._settings_reader = settings_reader or (lambda _user: ProbeSettings())
        self._recent = recent
        self._clock = clock
        self._min_interval = float(min_interval_seconds)
        self._spawn = spawn
        self._guard = threading.Lock()
        self._last_attempt: dict[tuple[str, str], float] = {}
        self._locks: dict[str, threading.Lock] = {}

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._probes)

    def settings(self, user_id: str) -> ProbeSettings:
        try:
            return self._settings_reader(user_id)
        except Exception:  # noqa: BLE001 - settings failures fall back to the defaults
            return ProbeSettings()

    def has_probe(self, provider: str) -> bool:
        return normalize_provider_name(provider) in self._probes

    def run(self, user_id: str, provider: str, *, reason: str = "") -> str:
        """Run one probe now (synchronously) when allowed. Returns an ``OUTCOME_*`` string."""
        canon = normalize_provider_name(provider)
        probe = self._probes.get(canon)
        if probe is None:
            return OUTCOME_NO_PROBE
        if not self.settings(user_id).enabled:
            return OUTCOME_DISABLED
        key = (str(user_id), canon)
        with self._guard:
            lock = self._locks.setdefault(canon, threading.Lock())
            now = self._clock()
            last = self._last_attempt.get(key)
            if last is not None and now - last < self._min_interval:
                return OUTCOME_THROTTLED
            if lock.locked():
                return OUTCOME_BUSY
            lock.acquire()
            self._last_attempt[key] = now
        try:
            if self._recent is not None:
                try:
                    age = self._recent(str(user_id), canon)
                except Exception:  # noqa: BLE001
                    age = None
                if age is not None and age < self._min_interval:
                    return OUTCOME_THROTTLED
            if probe.disabled_reason is not None or not probe.available():
                return OUTCOME_UNAVAILABLE
            snapshots = probe.read()
            if not snapshots:
                return OUTCOME_EMPTY
            self._record(str(user_id), snapshots)
            logger.debug("quota probe %s ran for %s (%s)", canon, user_id, reason)
            return OUTCOME_RAN
        except Exception:  # noqa: BLE001 - never raise into request/turn paths
            logger.debug("quota probe %s failed", canon, exc_info=True)
            return OUTCOME_EMPTY
        finally:
            lock.release()

    def request(self, user_id: str, provider: str, *, reason: str = "") -> bool:
        """Schedule a probe in a background thread. Returns False when it would be skipped for sure."""
        canon = normalize_provider_name(provider)
        if canon not in self._probes or not self.settings(user_id).enabled:
            return False
        key = (str(user_id), canon)
        with self._guard:
            last = self._last_attempt.get(key)
            if last is not None and self._clock() - last < self._min_interval:
                return False
            lock = self._locks.get(canon)
            if lock is not None and lock.locked():
                return False
        self._spawn(lambda: self.run(user_id, canon, reason=reason))
        return True

    def request_all(self, user_id: str, *, reason: str = "") -> list[str]:
        return [provider for provider in self._probes if self.request(user_id, provider, reason=reason)]


def recent_probe_age_from_store(store: Any, clock: Callable[[], Any]) -> Callable[[str, str], float | None]:
    """Seconds since the newest active-probe snapshot of ``provider`` in the store (visible across processes)."""

    def _age(user_id: str, provider: str) -> float | None:
        newest = None
        for snap in store.latest(user_id, provider=provider):
            if snap.source in ACTIVE_SOURCES and (newest is None or snap.observed_at > newest):
                newest = snap.observed_at
        if newest is None:
            return None
        return max(0.0, (clock() - newest).total_seconds())

    return _age


__all__ = [
    "MIN_PROBE_INTERVAL_SECONDS",
    "OUTCOME_BUSY",
    "OUTCOME_DISABLED",
    "OUTCOME_EMPTY",
    "OUTCOME_NO_PROBE",
    "OUTCOME_RAN",
    "OUTCOME_THROTTLED",
    "OUTCOME_UNAVAILABLE",
    "ProbeCoordinator",
    "ProbeSettings",
    "recent_probe_age_from_store",
]
