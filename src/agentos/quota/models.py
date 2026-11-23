"""Domain models for subscription quota snapshots, windows, and status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Confidence(StrEnum):
    MEASURED = "measured"
    PARTIAL = "partial"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    kind: str
    used_percent: float | None
    resets_at: datetime | None
    status: str
    scope_model: str | None = None
    # Models covered by a scoped window (e.g. an Antigravity model group); empty when unknown.
    scope_models: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "used_percent": self.used_percent,
            "resets_at": self.resets_at.astimezone(UTC).isoformat() if self.resets_at else None,
            "status": self.status,
            "scope_model": self.scope_model,
            "scope_models": list(self.scope_models),
        }


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    provider: str
    window_kind: str
    used_percent: float | None
    status: str
    resets_at: datetime | None
    source: str
    observed_at: datetime
    scope_model: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class OrinUsageWindow:
    tokens: int = 0
    calls: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"tokens": int(self.tokens), "calls": int(self.calls)}


@dataclass(frozen=True, slots=True)
class OrinUsageSummary:
    five_hour: OrinUsageWindow = field(default_factory=OrinUsageWindow)
    seven_day: OrinUsageWindow = field(default_factory=OrinUsageWindow)

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {
            "five_hour": self.five_hour.to_dict(),
            "seven_day": self.seven_day.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProviderQuota:
    provider: str
    windows: tuple[QuotaWindow, ...]
    confidence: Confidence
    observed_at: datetime | None
    source: str | None
    orin_usage: OrinUsageSummary = field(default_factory=OrinUsageSummary)
    age_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "windows": [w.to_dict() for w in self.windows],
            "confidence": self.confidence.value,
            "observed_at": self.observed_at.astimezone(UTC).isoformat() if self.observed_at else None,
            "age_seconds": self.age_seconds,
            "source": self.source,
            "orin_usage": self.orin_usage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    max_percent: float | None
    rejected: bool
    confidence: Confidence
    observed_at: datetime | None
    resets_at: datetime | None = None


__all__ = [
    "Confidence",
    "OrinUsageSummary",
    "OrinUsageWindow",
    "ProviderQuota",
    "QuotaSnapshot",
    "QuotaStatus",
    "QuotaWindow",
]
