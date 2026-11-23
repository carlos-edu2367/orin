"""Subscription quota aggregation, passive ingestion, and query service."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from agentos.providers.cli_adapter import RateLimitState

from .active_probe import ActiveProbe
from .claude_reader import ClaudeUsageCacheReader
from .codex_reader import CodexQuotaReader
from .matching import scope_matches_model
from .models import (
    Confidence,
    OrinUsageSummary,
    ProviderQuota,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
)
from .normalize import (
    normalize_provider_name,
    normalize_resets_at,
    normalize_window_kind,
    snapshots_from_rate_limit_state,
)
from .probe_coordinator import ProbeCoordinator, ProbeSettings, recent_probe_age_from_store

logger = logging.getLogger("agentos.quota")

# Window status for a window whose ``resets_at`` already passed: it was renewed and nothing newer was read yet.
WINDOW_RENEWED = "renewed"

SUBSCRIPTION_PROVIDERS: tuple[str, ...] = (
    "anthropic-cli",
    "openai-codex",
    "google-antigravity",
)

# Error-only snapshots (e.g. Antigravity 429 without resets_at) expire after a reasonable grace window
_RECENT_ERROR_MAX_AGE = timedelta(hours=6)

# Process-wide notification bus keyed by store scope (database URL when available) so separate
# QuotaService instances sharing the same database (e.g., ChatWorker + API) propagate SSE updates.
_SHARED_LISTENERS_LOCK = threading.Lock()
_SHARED_LISTENERS: dict[str, list[Callable[[str, str], None]]] = {}


def _store_scope_key(store: object) -> str | None:
    engine = getattr(store, "_engine", None)
    url = getattr(engine, "url", None)
    if url is not None:
        return str(url)
    return None


class QuotaStorePort(Protocol):
    def add(self, user_id: str, snapshot: QuotaSnapshot) -> None: ...
    def add_many(self, user_id: str, snapshots: Sequence[QuotaSnapshot]) -> int: ...
    def latest(self, user_id: str, provider: str | None = None) -> list[QuotaSnapshot]: ...
    def orin_usage(self, user_id: str, provider: str, *, now: datetime | None = None) -> OrinUsageSummary: ...


def _snapshot_signature(snap: QuotaSnapshot) -> tuple[Any, ...]:
    return (
        snap.provider,
        snap.window_kind,
        snap.scope_model,
        snap.used_percent,
        snap.status,
        snap.resets_at,
        snap.source,
        snap.observed_at,
    )


def _is_snapshot_active_rejection(snap: QuotaSnapshot, now: datetime) -> bool:
    if snap.status != "rejected":
        return False
    if snap.resets_at is not None:
        return snap.resets_at > now
    return (now - snap.observed_at) <= _RECENT_ERROR_MAX_AGE


def _snapshot_models(snap: QuotaSnapshot) -> tuple[str, ...]:
    models = snap.raw.get("models") if isinstance(snap.raw, dict) else None
    if isinstance(models, (list, tuple)):
        return tuple(str(item) for item in models if isinstance(item, str) and item.strip())
    return ()


def _window_from_snapshot(snap: QuotaSnapshot, now: datetime) -> QuotaWindow:
    """A snapshot as a window; one whose reset time already passed was renewed, so its old percent is dropped."""
    models = _snapshot_models(snap)
    if snap.source != "error" and snap.resets_at is not None and snap.resets_at <= now:
        return QuotaWindow(
            kind=snap.window_kind,
            used_percent=None,
            resets_at=None,
            status=WINDOW_RENEWED,
            scope_model=snap.scope_model,
            scope_models=models,
        )
    return QuotaWindow(
        kind=snap.window_kind,
        used_percent=snap.used_percent,
        resets_at=snap.resets_at,
        status=snap.status,
        scope_model=snap.scope_model,
        scope_models=models,
    )


def _window_sort_key(win: QuotaWindow) -> tuple[int, str, str]:
    order = {"five_hour": 0, "seven_day": 1, "weekly": 2, "weekly_scoped": 3}
    return (order.get(win.kind, 9), win.kind, win.scope_model or "")


_SETTINGS_CACHE_SECONDS = 5.0


def read_probe_settings() -> ProbeSettings:
    """``quota.activeProbe.*`` from the user's settings files; defaults when anything is unreadable."""
    try:
        from agentos.settings.loader import load_effective_settings
        from agentos.settings.registry import resolve

        effective = load_effective_settings(None)
        enabled = resolve(effective, "quota.activeProbe.enabled").value
        minutes = resolve(effective, "quota.activeProbe.intervalMinutes").value
        return ProbeSettings(
            enabled=bool(enabled) if isinstance(enabled, bool) else True,
            interval_minutes=max(2, min(60, int(minutes))),
        )
    except Exception:  # noqa: BLE001 - settings problems never disable quota reading
        return ProbeSettings()


class QuotaService:
    """Coordinates passive file readers, active CLI probes, runtime events, storage, and status queries."""

    def __init__(
        self,
        store: QuotaStorePort,
        *,
        claude_reader: ClaudeUsageCacheReader | None = None,
        codex_reader: CodexQuotaReader | None = None,
        clock: Callable[[], datetime] | None = None,
        on_updated: Callable[[str, str], None] | None = None,
        active_probes: Sequence[ActiveProbe] | None = None,
        probe_settings_reader: Callable[[str], ProbeSettings] | None = None,
        probe_clock: Callable[[], float] | None = None,
        probe_spawn: Callable[[Callable[[], None]], Any] | None = None,
    ) -> None:
        self._store = store
        self._claude_reader = claude_reader or ClaudeUsageCacheReader()
        self._codex_reader = codex_reader or CodexQuotaReader()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, str], None]] = []
        if on_updated is not None:
            self._listeners.append(on_updated)
        self._probes: ProbeCoordinator | None = None
        if active_probes:
            extra: dict[str, Any] = {}
            if probe_clock is not None:
                extra["clock"] = probe_clock
            if probe_spawn is not None:
                extra["spawn"] = probe_spawn
            self._probes = ProbeCoordinator(
                active_probes,
                record=lambda user_id, snaps: self.record_snapshots(user_id, snaps, deduplicate=False),
                settings_reader=probe_settings_reader or self._cached_probe_settings,
                recent=recent_probe_age_from_store(self._store, self._now),
                **extra,
            )
        self._settings_cache: tuple[float, ProbeSettings] | None = None

    # -- active probes -------------------------------------------------------------------------

    def _cached_probe_settings(self, _user_id: str) -> ProbeSettings:
        import time

        cached = self._settings_cache
        now = time.monotonic()
        if cached is not None and now - cached[0] < _SETTINGS_CACHE_SECONDS:
            return cached[1]
        value = read_probe_settings()
        self._settings_cache = (now, value)
        return value

    @property
    def probe_coordinator(self) -> ProbeCoordinator | None:
        return self._probes

    def probe_settings(self, user_id: str) -> ProbeSettings:
        """Whether active probes are on and how often the poller should run them (minutes)."""
        if self._probes is None:
            return ProbeSettings(enabled=False)
        return self._probes.settings(user_id)

    def refresh_active(self, user_id: str, providers: Sequence[str] | None = None, *, reason: str = "") -> list[str]:
        """Schedule background probes (throttled, one in flight per subscription). Returns the providers scheduled."""
        if self._probes is None:
            return []
        scheduled: list[str] = []
        for provider in providers if providers is not None else self._probes.providers:
            try:
                if self._probes.request(user_id, provider, reason=reason):
                    scheduled.append(normalize_provider_name(provider))
            except Exception:  # noqa: BLE001 - probing never fails callers
                logger.debug("could not schedule quota probe for %s", provider, exc_info=True)
        return scheduled

    def after_turn(self, user_id: str, provider: str | None) -> None:
        """End of a turn: reread local files and probe only the subscription the turn used."""
        try:
            self.refresh_from_files(user_id)
        except Exception:  # noqa: BLE001
            logger.debug("passive quota refresh after turn failed", exc_info=True)
        if provider:
            self.refresh_active(user_id, [provider], reason="after_turn")

    def subscribe_updates(self, listener: Callable[[str, str], None]) -> Callable[[], None]:
        scope_key = _store_scope_key(self._store)
        with self._lock:
            self._listeners.append(listener)
        if scope_key is not None:
            with _SHARED_LISTENERS_LOCK:
                _SHARED_LISTENERS.setdefault(scope_key, []).append(listener)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass
            if scope_key is not None:
                with _SHARED_LISTENERS_LOCK:
                    bucket = _SHARED_LISTENERS.get(scope_key)
                    if bucket is not None:
                        try:
                            bucket.remove(listener)
                        except ValueError:
                            pass
                        if not bucket:
                            _SHARED_LISTENERS.pop(scope_key, None)

        return _unsubscribe

    def _notify(self, user_id: str, providers: set[str]) -> None:
        with self._lock:
            local_listeners = list(self._listeners)
        scope_key = _store_scope_key(self._store)
        shared_listeners: list[Callable[[str, str], None]] = []
        if scope_key is not None:
            with _SHARED_LISTENERS_LOCK:
                shared_listeners = list(_SHARED_LISTENERS.get(scope_key, ()))
        seen_ids: set[int] = set()
        listeners: list[Callable[[str, str], None]] = []
        for fn in (*local_listeners, *shared_listeners):
            if id(fn) not in seen_ids:
                seen_ids.add(id(fn))
                listeners.append(fn)
        for provider in sorted(providers):
            for listener in listeners:
                try:
                    listener(user_id, provider)
                except Exception:  # noqa: BLE001 - notification must never break ingestion
                    pass

    def _now(self) -> datetime:
        now = self._clock()
        return now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)

    def record_snapshots(
        self,
        user_id: str,
        snapshots: Sequence[QuotaSnapshot],
        *,
        deduplicate: bool = False,
    ) -> list[QuotaSnapshot]:
        items = list(snapshots)
        if not items:
            return []

        if deduplicate:
            existing_sigs = {
                _snapshot_signature(s) for s in self._store.latest(user_id)
            }
            items = [s for s in items if _snapshot_signature(s) not in existing_sigs]
            if not items:
                return []

        self._store.add_many(user_id, items)
        self._notify(user_id, {normalize_provider_name(s.provider) for s in items})
        return items

    def record_rate_limit_state(
        self,
        user_id: str,
        provider: str,
        state: RateLimitState,
        *,
        observed_at: datetime | None = None,
    ) -> list[QuotaSnapshot]:
        snaps = snapshots_from_rate_limit_state(
            provider,
            state,
            observed_at=observed_at or self._now(),
        )
        return self.record_snapshots(user_id, snaps, deduplicate=False)

    def record_quota_error(
        self,
        user_id: str,
        provider: str,
        *,
        resets_at: datetime | float | int | str | None = None,
        retry_after_seconds: float | None = None,
        message: str | None = None,
        window_kind: str = "unknown",
        observed_at: datetime | None = None,
    ) -> QuotaSnapshot:
        now = observed_at or self._now()
        canon_provider = normalize_provider_name(provider)
        resolved_reset = normalize_resets_at(resets_at)
        if resolved_reset is None and retry_after_seconds is not None and retry_after_seconds > 0:
            resolved_reset = now + timedelta(seconds=float(retry_after_seconds))

        raw_payload: dict[str, Any] = {}
        if message:
            raw_payload["message"] = str(message)[:500]
        if retry_after_seconds is not None:
            raw_payload["retry_after_seconds"] = float(retry_after_seconds)

        snap = QuotaSnapshot(
            provider=canon_provider,
            window_kind=normalize_window_kind(window_kind),
            used_percent=None,
            status="rejected",
            resets_at=resolved_reset,
            source="error",
            observed_at=now,
            scope_model=None,
            raw=raw_payload or None,
        )
        self.record_snapshots(user_id, (snap,), deduplicate=False)
        return snap

    def refresh_from_files(self, user_id: str) -> list[QuotaSnapshot]:
        """Read passive local caches (Claude `~/.claude.json` and Codex rollouts) and persist new snapshots."""
        collected: list[QuotaSnapshot] = []
        for reader in (self._claude_reader, self._codex_reader):
            try:
                collected.extend(reader.read())
            except Exception:  # noqa: BLE001 - passive readers never fail callers
                continue
        return self.record_snapshots(user_id, collected, deduplicate=True)

    def _build_provider_quota(
        self,
        user_id: str,
        provider: str,
        snapshots: Sequence[QuotaSnapshot],
        now: datetime,
    ) -> ProviderQuota:
        canon = normalize_provider_name(provider)
        orin_usage = self._store.orin_usage(user_id, canon, now=now)

        # Filter out expired error-only snapshots so stale 429s do not linger indefinitely
        relevant: list[QuotaSnapshot] = []
        for snap in snapshots:
            if snap.source == "error":
                if snap.resets_at is not None and snap.resets_at <= now:
                    continue
                if snap.resets_at is None and (now - snap.observed_at) > _RECENT_ERROR_MAX_AGE:
                    continue
            relevant.append(snap)

        windows = tuple(
            sorted(
                (_window_from_snapshot(snap, now) for snap in relevant),
                key=_window_sort_key,
            )
        )

        observed_at: datetime | None = None
        source: str | None = None
        if relevant:
            newest = max(relevant, key=lambda s: s.observed_at)
            observed_at = newest.observed_at
            source = newest.source

        has_percent = any(w.used_percent is not None for w in windows)
        if has_percent:
            confidence = Confidence.MEASURED
        elif windows:
            confidence = Confidence.PARTIAL
        elif orin_usage.seven_day.calls > 0 or orin_usage.five_hour.calls > 0:
            confidence = Confidence.ESTIMATED
            source = source or "orin_usage"
        else:
            confidence = Confidence.UNKNOWN

        age_seconds: int | None = None
        if observed_at is not None:
            age_seconds = max(0, int((now - observed_at).total_seconds()))

        return ProviderQuota(
            provider=canon,
            windows=windows,
            confidence=confidence,
            observed_at=observed_at,
            source=source,
            orin_usage=orin_usage,
            age_seconds=age_seconds,
        )

    def get_all_quotas(self, user_id: str, *, refresh: bool = False) -> list[ProviderQuota]:
        """All subscriptions with their windows.

        ``refresh=True`` rereads local files synchronously and schedules active probes in the background
        without waiting for them: the caller gets what is stored now and the probe's snapshots arrive through
        ``quota.updated``. A probe takes up to ~6 s (Claude) and blocking an HTTP request on a CLI would make the
        panel feel frozen. Windows already renewed (``resets_at`` passed) also schedule a throttled probe.
        """
        if refresh:
            self.refresh_from_files(user_id)
            self.refresh_active(user_id, reason="refresh")
        now = self._now()
        latest = self._store.latest(user_id)
        grouped: dict[str, list[QuotaSnapshot]] = {p: [] for p in SUBSCRIPTION_PROVIDERS}
        for snap in latest:
            canon = normalize_provider_name(snap.provider)
            grouped.setdefault(canon, []).append(snap)

        ordered_providers = list(SUBSCRIPTION_PROVIDERS) + [
            p for p in grouped if p not in SUBSCRIPTION_PROVIDERS
        ]
        quotas = [
            self._build_provider_quota(user_id, prov, grouped.get(prov, ()), now)
            for prov in ordered_providers
        ]
        if not refresh:
            expired = [q.provider for q in quotas if any(w.status == WINDOW_RENEWED for w in q.windows)]
            if expired:
                self.refresh_active(user_id, expired, reason="window_renewed")
        return quotas

    def status_for(
        self,
        user_id: str,
        provider: str,
        model_id: str | None = None,
    ) -> QuotaStatus:
        """Cross-package contract (spec 2.5): concise status for a subscription provider/model."""
        now = self._now()
        canon = normalize_provider_name(provider)
        latest = self._store.latest(user_id, provider=canon)
        pq = self._build_provider_quota(user_id, canon, latest, now)

        model_lower = str(model_id or "").strip().lower()
        matching_windows: list[QuotaWindow] = []
        for win in pq.windows:
            if win.scope_model is None:
                matching_windows.append(win)
            elif scope_matches_model(canon, win.scope_model, model_lower, scope_models=win.scope_models):
                matching_windows.append(win)

        percents = [w.used_percent for w in matching_windows if w.used_percent is not None]
        max_percent = max(percents) if percents else None

        rejected_windows = [
            s for s in latest if _is_snapshot_active_rejection(s, now)
            and (
                s.scope_model is None
                or scope_matches_model(canon, s.scope_model, model_lower, scope_models=_snapshot_models(s))
            )
        ]
        rejected = bool(rejected_windows)

        resets_candidates = [
            s.resets_at for s in rejected_windows if s.resets_at is not None
        ]
        if not resets_candidates:
            resets_candidates = [w.resets_at for w in matching_windows if w.resets_at is not None]
        resets_at = min(resets_candidates) if resets_candidates else None

        return QuotaStatus(
            max_percent=max_percent,
            rejected=rejected,
            confidence=pq.confidence,
            observed_at=pq.observed_at,
            resets_at=resets_at,
        )


__all__ = [
    "QuotaService",
    "QuotaStorePort",
    "SUBSCRIPTION_PROVIDERS",
    "WINDOW_RENEWED",
    "read_probe_settings",
]
