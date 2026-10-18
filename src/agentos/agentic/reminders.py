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
        self._keys: set[str] = set()
        self._max = max(1, int(max_items))

    def add(self, kind: str, text: str, *, key: str = "") -> bool:
        clean = str(text or "").strip()
        if not clean:
            return False
        dedupe = key or f"{kind}\x00{clean}"
        with self._lock:
            if dedupe in self._keys or len(self._items) >= self._max:
                return False
            self._keys.add(dedupe)
            self._items.append(Reminder(str(kind or "note"), clean))
        return True

    def drain(self) -> list[Reminder]:
        with self._lock:
            items, self._items = self._items, []
            self._keys = set()
        return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def render_reminders(items: Iterable[Reminder]) -> str:
    """One user-message body: each reminder in its own block, in arrival order."""
    return "\n".join(block for block in (system_reminder(item.text) for item in items) if block)


__all__ = ["MAX_REMINDER_CHARS", "REMINDER_CLOSE", "REMINDER_OPEN", "Reminder", "ReminderQueue", "render_reminders", "system_reminder"]
