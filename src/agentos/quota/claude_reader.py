"""Passive reader for Claude CLI `cachedUsageUtilization` via `ClaudeAccountReader`."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .claude_account import ClaudeAccountReader
from .models import QuotaSnapshot
from .normalize import (
    normalize_percent,
    normalize_resets_at,
    normalize_status,
    normalize_window_kind,
)


def _severity_to_status(severity: Any, used_percent: float | None) -> str:
    sev = str(severity or "").strip().lower()
    if sev in {"rejected", "exhausted", "exceeded", "limit_reached"}:
        return "rejected"
    if sev in {"warning", "warn", "allowed_warning"}:
        return "allowed_warning"
    if sev in {"normal", "ok", "allowed"}:
        return normalize_status("allowed", used_percent=used_percent)
    return normalize_status(None, used_percent=used_percent)


class ClaudeUsageCacheReader:
    """Produces `QuotaSnapshot` entries from Claude's `cachedUsageUtilization`."""

    def __init__(self, account_reader: ClaudeAccountReader | None = None) -> None:
        self._account_reader = account_reader or ClaudeAccountReader()

    @property
    def account_reader(self) -> ClaudeAccountReader:
        return self._account_reader

    def read(self) -> list[QuotaSnapshot]:
        state = self._account_reader.read()
        cache = state.usage_cache
        if not isinstance(cache, Mapping):
            return []
        util = cache.get("utilization")
        if not isinstance(util, Mapping):
            return []

        observed_at = normalize_resets_at(state.fetched_at_ms) or datetime.now(UTC)
        results: dict[tuple[str, str | None], QuotaSnapshot] = {}

        # Primary named windows (five_hour, seven_day)
        for raw_key in ("five_hour", "seven_day"):
            entry = util.get(raw_key)
            if not isinstance(entry, Mapping):
                continue
            used_percent = normalize_percent(entry.get("utilization"), scale="percent")
            resets_at = normalize_resets_at(entry.get("resets_at") or entry.get("resetsAt"))
            status = _severity_to_status(entry.get("severity") or entry.get("status"), used_percent)
            if used_percent is None and resets_at is None:
                continue
            kind = normalize_window_kind(raw_key)
            results[(kind, None)] = QuotaSnapshot(
                provider="anthropic-cli",
                window_kind=kind,
                used_percent=used_percent,
                status=status,
                resets_at=resets_at,
                source="claude_usage_cache",
                observed_at=observed_at,
                scope_model=None,
                raw={
                    "window": raw_key,
                    "utilization": used_percent,
                    "resets_at": entry.get("resets_at") or entry.get("resetsAt"),
                    "fetched_at_ms": state.fetched_at_ms,
                },
            )

        # Structured limits[] entries (weekly_all enriches seven_day severity; weekly_scoped carries scope_model)
        limits = util.get("limits")
        if isinstance(limits, list):
            for item in limits:
                if not isinstance(item, Mapping):
                    continue
                raw_kind = str(item.get("kind") or "").strip().lower()
                if not raw_kind:
                    continue
                pct_val = item.get("percent")
                if pct_val is None:
                    pct_val = item.get("utilization")
                used_percent = normalize_percent(pct_val, scale="percent")
                resets_at = normalize_resets_at(item.get("resets_at") or item.get("resetsAt"))
                status = _severity_to_status(item.get("severity") or item.get("status"), used_percent)

                scope_model: str | None = None
                scope = item.get("scope")
                if isinstance(scope, Mapping):
                    model = scope.get("model")
                    if isinstance(model, Mapping):
                        name = model.get("display_name")
                        if isinstance(name, str) and name.strip():
                            scope_model = name.strip()

                kind = normalize_window_kind(raw_kind)
                key = (kind, scope_model)
                existing = results.get(key)
                if existing is not None:
                    # Prefer explicit severity from limits[] (e.g. weekly_all severity: warning)
                    merged_status = status if item.get("severity") or item.get("status") else existing.status
                    merged_pct = used_percent if used_percent is not None else existing.used_percent
                    merged_reset = resets_at or existing.resets_at
                    results[key] = QuotaSnapshot(
                        provider="anthropic-cli",
                        window_kind=kind,
                        used_percent=merged_pct,
                        status=merged_status,
                        resets_at=merged_reset,
                        source="claude_usage_cache",
                        observed_at=observed_at,
                        scope_model=scope_model,
                        raw={
                            "kind": raw_kind,
                            "percent": merged_pct,
                            "severity": item.get("severity"),
                            "resets_at": item.get("resets_at") or item.get("resetsAt"),
                            "scope_model": scope_model,
                            "fetched_at_ms": state.fetched_at_ms,
                        },
                    )
                else:
                    if used_percent is None and resets_at is None:
                        continue
                    results[key] = QuotaSnapshot(
                        provider="anthropic-cli",
                        window_kind=kind,
                        used_percent=used_percent,
                        status=status,
                        resets_at=resets_at,
                        source="claude_usage_cache",
                        observed_at=observed_at,
                        scope_model=scope_model,
                        raw={
                            "kind": raw_kind,
                            "percent": used_percent,
                            "severity": item.get("severity"),
                            "resets_at": item.get("resets_at") or item.get("resetsAt"),
                            "scope_model": scope_model,
                            "fetched_at_ms": state.fetched_at_ms,
                        },
                    )

        return list(results.values())


__all__ = ["ClaudeUsageCacheReader"]
