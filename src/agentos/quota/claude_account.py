"""Read-only, allowlisted reader for `~/.claude.json` shared by WS-E2 and WS-E5.

Security invariant: `~/.claude.json` may contain OAuth tokens and account credentials.
This reader ONLY extracts:
  - `oauthAccount.hasExtraUsageEnabled`
  - `cachedGrowthBookFeatures.tengu_usage_overage_included_models`
  - a sanitized projection of `cachedUsageUtilization` (usage percentages, reset timestamps,
    limit entries, and `fetchedAtMs`)
Everything else in the parsed JSON is discarded immediately and never stored, returned, or logged.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MAX_CLAUDE_JSON_BYTES = 16 * 1024 * 1024  # 16 MB safety ceiling

_ALLOWED_WINDOW_FIELDS = ("utilization", "resets_at", "resetsAt", "status", "severity")
_ALLOWED_UTILIZATION_WINDOW_KEYS = (
    "five_hour",
    "seven_day",
    "seven_day_opus",
    "seven_day_sonnet",
    "seven_day_haiku",
    "seven_day_fable",
)


@dataclass(frozen=True, slots=True)
class ClaudeAccountState:
    extra_usage_enabled: bool | None
    overage_models: frozenset[str]
    usage_cache: dict[str, Any] | None
    fetched_at_ms: int | None


_EMPTY_STATE = ClaudeAccountState(
    extra_usage_enabled=None,
    overage_models=frozenset(),
    usage_cache=None,
    fetched_at_ms=None,
)


def _sanitize_window_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in _ALLOWED_WINDOW_FIELDS:
        if key in entry and entry[key] is not None:
            val = entry[key]
            if isinstance(val, (int, float, str, bool)):
                out[key] = val
    return out or None


def _sanitize_limit_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in ("kind", "percent", "utilization", "severity", "status", "resets_at", "resetsAt"):
        if key in entry and entry[key] is not None:
            val = entry[key]
            if isinstance(val, (int, float, str, bool)):
                out[key] = val
    scope = entry.get("scope")
    if isinstance(scope, Mapping):
        model = scope.get("model")
        if isinstance(model, Mapping):
            display_name = model.get("display_name") or model.get("displayName") or model.get("name")
            if isinstance(display_name, str) and display_name.strip():
                out["scope"] = {"model": {"display_name": display_name.strip()}}
    return out or None


def _sanitize_usage_cache(raw_cache: Any) -> tuple[dict[str, Any] | None, int | None]:
    if not isinstance(raw_cache, Mapping):
        return None, None

    fetched_raw = raw_cache.get("fetchedAtMs")
    if fetched_raw is None:
        fetched_raw = raw_cache.get("fetched_at_ms")
    fetched_at_ms: int | None = None
    if isinstance(fetched_raw, (int, float)) and not isinstance(fetched_raw, bool) and fetched_raw > 0:
        fetched_at_ms = int(fetched_raw)

    sanitized: dict[str, Any] = {}
    if fetched_at_ms is not None:
        sanitized["fetchedAtMs"] = fetched_at_ms

    util_raw = raw_cache.get("utilization")
    if isinstance(util_raw, Mapping):
        util_out: dict[str, Any] = {}
        for win_key in _ALLOWED_UTILIZATION_WINDOW_KEYS:
            cleaned = _sanitize_window_entry(util_raw.get(win_key))
            if cleaned is not None:
                util_out[win_key] = cleaned

        extra_raw = util_raw.get("extra_usage")
        if isinstance(extra_raw, Mapping):
            extra_out: dict[str, Any] = {}
            for key in ("is_enabled", "enabled", "utilization", "used_credits", "monthly_limit"):
                if key in extra_raw and isinstance(extra_raw[key], (bool, int, float, str)):
                    extra_out[key] = extra_raw[key]
            if extra_out:
                util_out["extra_usage"] = extra_out

        limits_raw = util_raw.get("limits")
        if isinstance(limits_raw, list):
            cleaned_limits: list[dict[str, Any]] = []
            for item in limits_raw[:64]:
                cleaned = _sanitize_limit_entry(item)
                if cleaned is not None:
                    cleaned_limits.append(cleaned)
            if cleaned_limits:
                util_out["limits"] = cleaned_limits

        if util_out:
            sanitized["utilization"] = util_out

    if not sanitized:
        return None, fetched_at_ms
    return sanitized, fetched_at_ms


def _extract_state(data: Any) -> ClaudeAccountState:
    if not isinstance(data, Mapping):
        return _EMPTY_STATE

    extra_usage_enabled: bool | None = None
    oauth = data.get("oauthAccount")
    if isinstance(oauth, Mapping):
        val = oauth.get("hasExtraUsageEnabled")
        if isinstance(val, bool):
            extra_usage_enabled = val

    overage_models: frozenset[str] = frozenset()
    gb = data.get("cachedGrowthBookFeatures")
    if isinstance(gb, Mapping):
        raw_models = gb.get("tengu_usage_overage_included_models")
        if isinstance(raw_models, list):
            overage_models = frozenset(
                str(item).strip()
                for item in raw_models
                if isinstance(item, str) and item.strip()
            )

    usage_cache, fetched_at_ms = _sanitize_usage_cache(data.get("cachedUsageUtilization"))

    return ClaudeAccountState(
        extra_usage_enabled=extra_usage_enabled,
        overage_models=overage_models,
        usage_cache=usage_cache,
        fetched_at_ms=fetched_at_ms,
    )


class ClaudeAccountReader:
    """Thread-safe, mtime+size cached, read-only reader for `~/.claude.json`."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_bytes: int = DEFAULT_MAX_CLAUDE_JSON_BYTES,
    ) -> None:
        self._path = Path(path) if path is not None else Path.home() / ".claude.json"
        self._max_bytes = max(1, int(max_bytes))
        self._lock = threading.Lock()
        self._cached_key: tuple[int, int] | None = None
        self._cached_state: ClaudeAccountState | None = None

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> ClaudeAccountState:
        try:
            st = self._path.stat()
        except OSError:
            return _EMPTY_STATE

        if st.st_size <= 0 or st.st_size > self._max_bytes:
            return _EMPTY_STATE

        cache_key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._cached_key == cache_key and self._cached_state is not None:
                return self._cached_state

        try:
            raw_bytes = self._path.read_bytes()
        except OSError:
            return _EMPTY_STATE

        if len(raw_bytes) > self._max_bytes:
            return _EMPTY_STATE

        try:
            data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        except (ValueError, TypeError):
            return _EMPTY_STATE

        state = _extract_state(data)
        del data
        del raw_bytes

        with self._lock:
            self._cached_key = cache_key
            self._cached_state = state
        return state


__all__ = [
    "ClaudeAccountReader",
    "ClaudeAccountState",
    "DEFAULT_MAX_CLAUDE_JSON_BYTES",
]
