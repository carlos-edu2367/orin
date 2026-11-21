"""Quarantine write policy for untrusted external memory sources (WS-D4 / D-RF8)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .models_write import MemoryWriteContext, WriteDecision

QUARANTINED_SOURCE_KINDS: frozenset[str] = frozenset({"tool", "web", "file"})
QUARANTINE_SETTING_KEY = "memory.quarantine.enabled"


def is_quarantine_enabled(settings: Any = None, *, default: bool = True) -> bool:
    """Read ``memory.quarantine.enabled`` from ``EffectiveSettings`` or a plain mapping."""
    if settings is None:
        return bool(default)
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            value = getter(QUARANTINE_SETTING_KEY, None)
            if isinstance(value, bool):
                return value
        except TypeError:
            pass
    if isinstance(settings, Mapping):
        direct = settings.get(QUARANTINE_SETTING_KEY)
        if isinstance(direct, bool):
            return direct
        cursor: Any = settings
        for part in QUARANTINE_SETTING_KEY.split("."):
            if not isinstance(cursor, Mapping):
                cursor = None
                break
            cursor = cursor.get(part)
        if isinstance(cursor, bool):
            return cursor
    return bool(default)


class QuarantinePolicy:
    """Redirects memory writes from ``tool|web|file`` sources to ``status="quarantined"``.

    Active whenever ``memory.quarantine.enabled`` is True (default). Consolidation
    (``source_kind="consolidator"``), explicit user requests (``"user"``), and clean
    agent writes (``"agent"``) remain ``status="active"``.
    """

    def __init__(
        self,
        *,
        enabled: bool | Callable[[], bool] = True,
        settings_provider: Callable[[], Any] | None = None,
        on_quarantined: Callable[[Any, MemoryWriteContext, WriteDecision], None] | None = None,
    ) -> None:
        self._enabled = enabled
        self._settings_provider = settings_provider
        self._on_quarantined = on_quarantined

    def is_enabled(self) -> bool:
        if self._settings_provider is not None:
            try:
                settings = self._settings_provider()
            except Exception:  # noqa: BLE001 - settings lookup failure fails closed (quarantine stays on)
                settings = None
            if settings is not None:
                fallback = bool(self._enabled()) if callable(self._enabled) else bool(self._enabled)
                return is_quarantine_enabled(settings, default=fallback)
        return bool(self._enabled()) if callable(self._enabled) else bool(self._enabled)

    def decide(self, candidate: Any, context: MemoryWriteContext) -> WriteDecision:
        if not self.is_enabled():
            return WriteDecision(action="add", target_id=None, status="active", reason="quarantine_disabled")

        source_kind = str(getattr(context, "source_kind", "") or "").strip().lower()
        if source_kind in QUARANTINED_SOURCE_KINDS:
            decision = WriteDecision(
                action="quarantine",
                target_id=None,
                status="quarantined",
                reason=f"untrusted_source:{source_kind}",
            )
            if self._on_quarantined is not None:
                try:
                    self._on_quarantined(candidate, context, decision)
                except Exception:  # noqa: BLE001 - notification callback never breaks persistence
                    pass
            return decision

        return WriteDecision(action="add", target_id=None, status="active", reason="trusted_source")


__all__ = [
    "QUARANTINED_SOURCE_KINDS",
    "QUARANTINE_SETTING_KEY",
    "QuarantinePolicy",
    "is_quarantine_enabled",
]
