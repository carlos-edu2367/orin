"""Delivery acknowledgement for direct messages injected into a subagent's ReminderQueue (WS-B7).

A message enters ``ReminderQueue`` with ``key="user-msg:<control_id>"`` and state ``injected``.
Only when the runtime actually drains that reminder into a model request or CLI tool-batch note
does ``ack_delivered_reminders`` call the acknowledger, transitioning ``injected -> delivered``,
recording UTC ``delivered_at``, and emitting ``agent.message_delivered`` idempotently.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
import logging
import threading
from typing import Any

from agentos.agentic.reminders import Reminder

logger = logging.getLogger("agentos.agentic.message_ack")

USER_MESSAGE_KEY_PREFIX = "user-msg:"


def control_id_from_reminder_key(key: str | None) -> str | None:
    """Extract ``control_id`` from a ``user-msg:<control_id>`` reminder key, else None."""
    raw = str(key or "")
    if not raw.startswith(USER_MESSAGE_KEY_PREFIX):
        return None
    control_id = raw[len(USER_MESSAGE_KEY_PREFIX) :].strip()
    return control_id or None


class MessageAcknowledger:
    """Idempotently transitions ``injected -> delivered`` for drained message controls."""

    def __init__(
        self,
        store: Any,
        *,
        on_delivered: Callable[[str, datetime], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._on_delivered = on_delivered
        self._clock = clock
        self._lock = threading.Lock()
        self._acked: set[str] = set()

    def _now(self) -> datetime:
        if self._clock is not None:
            stamp = self._clock()
            if isinstance(stamp, datetime):
                return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
        return datetime.now(UTC)

    def ack(self, control_id: str, *, delivered_at: datetime | None = None) -> bool:
        """Mark ``control_id`` as ``delivered`` with UTC ``delivered_at`` once.

        Returns True iff this call performed the transition; subsequent calls are no-ops returning False.
        """
        cid = str(control_id or "").strip()
        if not cid:
            return False
        with self._lock:
            if cid in self._acked:
                return False
            stamp = (
                delivered_at.replace(tzinfo=UTC)
                if isinstance(delivered_at, datetime) and delivered_at.tzinfo is None
                else (delivered_at.astimezone(UTC) if isinstance(delivered_at, datetime) else self._now())
            )
            transitioned = False
            if self._store is not None:
                mark_fn = getattr(self._store, "mark_delivered", None)
                if callable(mark_fn):
                    transitioned = bool(mark_fn(cid, delivered_at=stamp))
                else:
                    set_fn = getattr(self._store, "set_outcome", None)
                    if callable(set_fn):
                        set_fn(cid, "delivered")
                        transitioned = True
            else:
                transitioned = True
            self._acked.add(cid)
        if transitioned and self._on_delivered is not None:
            try:
                self._on_delivered(cid, stamp)
            except Exception:  # noqa: BLE001 - ack telemetry must never break a turn
                logger.debug("on_delivered callback failed for %s", cid, exc_info=True)
        return transitioned

    def is_acked(self, control_id: str) -> bool:
        with self._lock:
            return str(control_id or "").strip() in self._acked


def ack(
    store: Any,
    control_id: str,
    *,
    on_delivered: Callable[[str, datetime], None] | None = None,
    delivered_at: datetime | None = None,
) -> bool:
    """One-shot convenience helper around :class:`MessageAcknowledger`."""
    return MessageAcknowledger(store, on_delivered=on_delivered).ack(control_id, delivered_at=delivered_at)


def ack_delivered_reminders(
    reminders: Iterable[Reminder],
    ack_callback: Callable[[str], Any] | None,
) -> list[str]:
    """Acknowledge every drained ``Reminder`` carrying a ``user-msg:<control_id>`` key."""
    if not callable(ack_callback):
        return []
    acked: list[str] = []
    for item in reminders:
        cid = control_id_from_reminder_key(getattr(item, "key", ""))
        if not cid:
            continue
        try:
            ack_callback(cid)
            acked.append(cid)
        except Exception:  # noqa: BLE001 - ack failure must never break model delivery
            logger.debug("reminder ack callback failed for %s", cid, exc_info=True)
    return acked


__all__ = [
    "MessageAcknowledger",
    "USER_MESSAGE_KEY_PREFIX",
    "ack",
    "ack_delivered_reminders",
    "control_id_from_reminder_key",
]
