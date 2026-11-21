"""Normalization helpers for subscription quota metrics across providers."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from agentos.providers.cli_adapter import RateLimitState

from .models import QuotaSnapshot

_PROVIDER_ALIASES: dict[str, str] = {
    "anthropic": "anthropic-cli",
    "anthropic-cli": "anthropic-cli",
    "claude": "anthropic-cli",
    "claude-cli": "anthropic-cli",
    "claude_cli": "anthropic-cli",
    "openai-codex": "openai-codex",
    "codex": "openai-codex",
    "codex-cli": "openai-codex",
    "codex_cli": "openai-codex",
    "google-antigravity": "google-antigravity",
    "antigravity": "google-antigravity",
    "antigravity-cli": "google-antigravity",
    "antigravity_cli": "google-antigravity",
    "agy": "google-antigravity",
}

_WINDOW_KIND_ALIASES: dict[str, str] = {
    "five_hour": "five_hour",
    "5h": "five_hour",
    "5_hour": "five_hour",
    "session": "five_hour",
    "seven_day": "seven_day",
    "7d": "seven_day",
    "7_day": "seven_day",
    "weekly_all": "seven_day",
    "weekly": "weekly",
    "weekly_scoped": "weekly_scoped",
}

_MINUTES_TO_WINDOW_KIND: dict[int, str] = {
    300: "five_hour",
    10080: "seven_day",
}


def normalize_provider_name(provider: str) -> str:
    cleaned = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(cleaned, cleaned)


def normalize_percent(
    value: Any,
    *,
    scale: Literal["auto", "fraction", "percent"] = "auto",
) -> float | None:
    """Normalize a quota utilization value to the 0.0..100.0 percentage scale.

    - ``scale="fraction"``: used for Claude CLI ``unifiedWindows.*.utilization`` where
      ``0.45`` means 45% and ``1.0`` means 100%. Values ``> 1.0`` are kept as percentages
      in case a future CLI switches units.
    - ``scale="percent"``: used for ``cachedUsageUtilization`` (e.g. ``77``) and Codex
      rollout ``used_percent`` (e.g. ``28.5``, ``1.0`` = 1%).
    - ``scale="auto"``: values strictly between ``0`` and ``1`` are treated as fractions.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None

    if scale == "fraction":
        if 0.0 <= num <= 1.0:
            num *= 100.0
    elif scale == "auto":
        if 0.0 < num < 1.0:
            num *= 100.0

    clamped = max(0.0, min(100.0, num))
    return round(clamped, 4)


def normalize_window_kind(
    raw_kind: str | None = None,
    *,
    window_minutes: int | float | None = None,
) -> str:
    if (
        window_minutes is not None
        and not isinstance(window_minutes, bool)
        and isinstance(window_minutes, (int, float))
        and math.isfinite(float(window_minutes))
    ):
        minutes_int = int(round(float(window_minutes)))
        if minutes_int in _MINUTES_TO_WINDOW_KIND:
            return _MINUTES_TO_WINDOW_KIND[minutes_int]
    cleaned = str(raw_kind or "").strip().lower()
    if not cleaned:
        return "unknown"
    return _WINDOW_KIND_ALIASES.get(cleaned, cleaned)


def normalize_resets_at(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        num = float(value)
        if not math.isfinite(num) or num <= 0:
            return None
        if num > 10_000_000_000:
            num /= 1000.0
        try:
            return datetime.fromtimestamp(num, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def normalize_status(raw_status: str | None, *, used_percent: float | None = None) -> str:
    cleaned = str(raw_status or "").strip().lower()
    if cleaned in {"rejected", "exhausted", "rate_limited", "blocked"}:
        return "rejected"
    if cleaned in {"allowed_warning", "warning"}:
        return "allowed_warning"
    if cleaned in {"allowed", "normal", "ok"}:
        if used_percent is not None and used_percent >= 100.0:
            return "rejected"
        return "allowed"
    if used_percent is not None:
        if used_percent >= 100.0:
            return "rejected"
        if used_percent >= 80.0:
            return "allowed_warning"
        return "allowed"
    return "unknown"


def snapshots_from_rate_limit_state(
    provider: str,
    state: RateLimitState,
    *,
    observed_at: datetime | None = None,
) -> list[QuotaSnapshot]:
    """Convert a provider-layer `RateLimitState` into normalized `QuotaSnapshot` entries."""
    canon_provider = normalize_provider_name(provider)
    now = observed_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    snapshots: list[QuotaSnapshot] = []
    if state.windows:
        for win in state.windows:
            kind = normalize_window_kind(win.kind)
            used_pct = normalize_percent(win.utilization, scale="fraction")
            win_status = normalize_status(
                win.status or state.status,
                used_percent=used_pct,
            )
            resets_at = normalize_resets_at(win.resets_at if win.resets_at is not None else state.resets_at)
            snapshots.append(
                QuotaSnapshot(
                    provider=canon_provider,
                    window_kind=kind,
                    used_percent=used_pct,
                    status=win_status,
                    resets_at=resets_at,
                    source="rate_limit_event",
                    observed_at=now,
                    raw={
                        "kind": win.kind,
                        "utilization": win.utilization,
                        "status": win.status or state.status,
                        "resets_at": win.resets_at,
                    },
                )
            )
        return snapshots

    kind = normalize_window_kind(state.rate_limit_type)
    status = normalize_status(state.status)
    resets_at = normalize_resets_at(state.resets_at)
    if status == "unknown" and resets_at is None and kind == "unknown":
        return []
    snapshots.append(
        QuotaSnapshot(
            provider=canon_provider,
            window_kind=kind,
            used_percent=None,
            status=status,
            resets_at=resets_at,
            source="rate_limit_event",
            observed_at=now,
            raw={
                "status": state.status,
                "rate_limit_type": state.rate_limit_type,
                "resets_at": state.resets_at,
            },
        )
    )
    return snapshots


__all__ = [
    "normalize_percent",
    "normalize_provider_name",
    "normalize_resets_at",
    "normalize_status",
    "normalize_window_kind",
    "snapshots_from_rate_limit_state",
]
