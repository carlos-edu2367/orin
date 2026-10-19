"""The one way Orin puts out-of-band context in front of the model: a <system-reminder> block.

Hook context, files that changed on disk, memory loaded on demand and plan-mode
notes all reach the model through here, so it always sees one shape, and text
that came from a file or a hook can never close the block early or forge a new one.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass

REMINDER_OPEN = "<system-reminder>"
REMINDER_CLOSE = "</system-reminder>"
MAX_REMINDER_CHARS = 10_000
MAX_PENDING_REMINDERS = 32
# W4-R9: monitor reminders may take at most half the queue and a fixed character budget per request.
MONITOR_KIND = "monitor"
MAX_MONITOR_REMINDERS = MAX_PENDING_REMINDERS // 2
MONITOR_CHAR_BUDGET = 12_000
# Any spelling of the reminder tag, opening or closing: case, inner spaces and a short attribute list. Text that
# came from a hook or a file must never close the block early or open a new one.
_REMINDER_TAG = re.compile(r"<([\s/]*)system\s*[-_]\s*reminder\b[^>]{0,200}>", re.IGNORECASE)


def _neutralize(text: str) -> str:
    return _REMINDER_TAG.sub(lambda match: "</system_reminder>" if "/" in match.group(1) else "<system_reminder>", text)


def system_reminder(text: str, *, limit: int = MAX_REMINDER_CHARS) -> str:
    """``text`` wrapped in one reminder block, or "" when there is nothing to say."""
    body = _neutralize(str(text or "")).strip()
    if not body:
        return ""
    if len(body) > limit:
        body = body[: max(0, int(limit))] + "\n[truncated]"
    return f"{REMINDER_OPEN}\n{body}\n{REMINDER_CLOSE}"


@dataclass(frozen=True, slots=True)
class Reminder:
    kind: str
    text: str


class ReminderQueue:
    """Reminders waiting for the next model request. Safe to fill from the parallel tool pool."""

    def __init__(self, *, max_items: int = MAX_PENDING_REMINDERS) -> None:
        self._lock = threading.Lock()
        self._items: list[Reminder] = []
        # dedupe key -> position in _items, so a pending reminder can be rewritten in place
        self._keys: dict[str, int] = {}
        self._max = max(1, int(max_items))

    def add(self, kind: str, text: str, *, key: str = "") -> bool:
        clean = str(text or "").strip()
        if not clean:
            return False
        dedupe = key or f"{kind}\x00{clean}"
        with self._lock:
            if dedupe in self._keys or len(self._items) >= self._max or not self._fits_locked(kind, clean):
                return False
            self._keys[dedupe] = len(self._items)
            self._items.append(Reminder(str(kind or "note"), clean))
        return True

    def replace(self, key: str, kind: str, text: str) -> bool:
        """Rewrite the pending reminder stored under ``key`` in place. False when none is pending (it was drained)."""
        clean = str(text or "").strip()
        with self._lock:
            index = self._keys.get(key)
            if index is None or not clean or not self._fits_locked(kind, clean, replacing=index):
                return False
            self._items[index] = Reminder(str(kind or "note"), clean)
        return True

    def drain(self) -> list[Reminder]:
        with self._lock:
            items, self._items = self._items, []
            self._keys = {}
        return items

    def _fits_locked(self, kind: str, text: str, *, replacing: int | None = None) -> bool:
        """Monitor reminders take at most half the queue and MONITOR_CHAR_BUDGET characters together (W4-R9)."""
        if kind != MONITOR_KIND:
            return True
        others = [item for index, item in enumerate(self._items) if item.kind == MONITOR_KIND and index != replacing]
        if replacing is None and len(others) >= MAX_MONITOR_REMINDERS:
            return False
        return sum(len(item.text) for item in others) + len(text) <= MONITOR_CHAR_BUDGET

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def render_reminders(items: Iterable[Reminder]) -> str:
    """One user-message body: each reminder in its own block, in arrival order."""
    return "\n".join(block for block in (system_reminder(item.text) for item in items) if block)


__all__ = [
    "MAX_MONITOR_REMINDERS", "MAX_REMINDER_CHARS", "MONITOR_CHAR_BUDGET", "MONITOR_KIND", "REMINDER_CLOSE", "REMINDER_OPEN",
    "Reminder", "ReminderQueue", "render_reminders", "system_reminder",
]
