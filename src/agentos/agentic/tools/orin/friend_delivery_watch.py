"""Delivery status observer for messages sent by Orin Friend into live sessions (WS-B8)."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import threading
from typing import Any, Literal

from sqlalchemy import func, insert, select

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.persistence.postgres.schema import (
    conversation_activity_events,
    conversation_events,
    conversation_turns,
)

logger = logging.getLogger(__name__)

DeliveryWatchStatus = Literal["injected", "promoted", "blocked"]

STATUS_INJECTED: DeliveryWatchStatus = "injected"
STATUS_PROMOTED: DeliveryWatchStatus = "promoted"
STATUS_BLOCKED: DeliveryWatchStatus = "blocked"

_MAX_HISTORY = 256


@dataclass(frozen=True, slots=True)
class FriendDeliveryNotification:
    status: DeliveryWatchStatus
    user_id: str
    friend_conversation_id: str | None
    target_conversation_id: str
    target_turn_id: str | None
    queue_id: str
    summary: str
    new_turn_id: str | None = None
    reason: str | None = None
    created_at: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "queue_id": self.queue_id,
            "target_conversation_id": self.target_conversation_id,
            "target_turn_id": self.target_turn_id,
            "new_turn_id": self.new_turn_id,
            "friend_conversation_id": self.friend_conversation_id,
            "reason": self.reason,
            "summary": self.summary,
            "created_at": self.created_at,
        }


def _format_summary(
    status: DeliveryWatchStatus,
    *,
    target_conversation_id: str,
    reason: str | None = None,
) -> str:
    if status == STATUS_INJECTED:
        return (
            f"A mensagem enviada à sessão {target_conversation_id} "
            "foi entregue no turno em andamento."
        )
    if status == STATUS_PROMOTED:
        return (
            f"O turno em andamento da sessão {target_conversation_id} encerrou antes de ler a mensagem; "
            "ela ficou para o próximo turno."
        )
    clean_reason = str(reason or "").strip()
    if clean_reason:
        return (
            f"A mensagem enviada à sessão {target_conversation_id} foi bloqueada por um hook "
            f"({clean_reason}) e não foi entregue."
        )
    return (
        f"A mensagem enviada à sessão {target_conversation_id} foi bloqueada por um hook "
        "e não foi entregue."
    )


class FriendDeliveryWatcherRegistry:
    """Routes Friend live-delivery transitions (`injected`, `promoted`, `blocked`) back to Orin Friend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[FriendDeliveryNotification], Any]] = []
        self._reminder_queues: dict[str, Any] = {}
        self._watch_callbacks: dict[str, Callable[..., Any]] = {}
        self._pending_reminders: dict[str, list[FriendDeliveryNotification]] = {}
        self._drained_durable_keys: dict[str, set[tuple[str, str]]] = {}
        self._history: deque[FriendDeliveryNotification] = deque(maxlen=_MAX_HISTORY)
        self._emitted_keys: deque[tuple[str, str]] = deque(maxlen=_MAX_HISTORY * 4)
        self._emitted_set: set[tuple[str, str]] = set()

    def register_target(
        self,
        *,
        friend_conversation_id: str | None,
        reminder_queue: Any = None,
        on_watch_notify: Callable[..., Any] | None = None,
    ) -> None:
        clean_fcid = str(friend_conversation_id or "").strip()
        if not clean_fcid:
            return
        with self._lock:
            if reminder_queue is not None:
                self._reminder_queues[clean_fcid] = reminder_queue
            if callable(on_watch_notify):
                self._watch_callbacks[clean_fcid] = on_watch_notify

    def unregister_target(
        self,
        *,
        friend_conversation_id: str | None,
        reminder_queue: Any = None,
    ) -> None:
        clean_fcid = str(friend_conversation_id or "").strip()
        if not clean_fcid:
            return
        with self._lock:
            current = self._reminder_queues.get(clean_fcid)
            if reminder_queue is None or current is reminder_queue:
                self._reminder_queues.pop(clean_fcid, None)

    def drain_pending_for_friend(
        self,
        friend_conversation_id: str | None,
        reminder_queue: Any,
        *,
        store: Any = None,
        user_id: str | None = None,
    ) -> int:
        """Deliver any buffered or durably persisted Friend delivery confirmations into ``reminder_queue``."""
        clean_fcid = str(friend_conversation_id or "").strip()
        if not clean_fcid or reminder_queue is None or not callable(getattr(reminder_queue, "add", None)):
            return 0
        added = 0
        seen_keys: set[tuple[str, str]] = set()
        with self._lock:
            buffered = self._pending_reminders.pop(clean_fcid, [])
            already_drained = self._drained_durable_keys.setdefault(clean_fcid, set())
            for item in buffered:
                key_pair = (item.queue_id, item.status)
                if key_pair in seen_keys:
                    continue
                seen_keys.add(key_pair)
                already_drained.add(key_pair)
        for item in buffered:
            try:
                if reminder_queue.add(
                    "friend_delivery",
                    item.summary,
                    key=f"friend_delivery:{item.queue_id}:{item.status}",
                ):
                    added += 1
            except Exception:  # noqa: BLE001
                logger.debug("Failed to drain buffered friend delivery reminder", exc_info=True)

        # Durable fallback: recover delivery_status activity events recorded after the Friend turn ended
        # (including across process restarts when the in-memory buffer is empty).
        engine = getattr(store, "_engine", None) if store is not None else None
        clean_uid = str(user_id or "").strip()
        if engine is not None and clean_uid:
            try:
                with engine.connect() as conn:
                    last_finished_row = (
                        conn.execute(
                            select(conversation_turns.c.finished_at)
                            .where(
                                conversation_turns.c.conversation_id == clean_fcid,
                                conversation_turns.c.user_id == clean_uid,
                                conversation_turns.c.finished_at.is_not(None),
                            )
                            .order_by(conversation_turns.c.finished_at.desc())
                            .limit(1)
                        )
                        .first()
                    )
                    stmt = select(
                        conversation_activity_events.c.summary,
                        conversation_activity_events.c.payload,
                        conversation_activity_events.c.created_at,
                    ).where(
                        conversation_activity_events.c.conversation_id == clean_fcid,
                        conversation_activity_events.c.user_id == clean_uid,
                        conversation_activity_events.c.event_type == AgentActivityEventType.FRIEND_DELIVERY_STATUS.value,
                    )
                    if last_finished_row is not None and last_finished_row[0] is not None:
                        stmt = stmt.where(conversation_activity_events.c.created_at >= last_finished_row[0])
                    rows = conn.execute(stmt.order_by(conversation_activity_events.c.id.asc()).limit(32)).mappings().all()
                for row in rows:
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    qid = str(payload.get("queue_id") or "").strip()
                    st = str(payload.get("status") or "").strip()
                    summary_text = str(row.get("summary") or payload.get("summary") or "").strip()
                    if not qid or not st or not summary_text:
                        continue
                    key_pair = (qid, st)
                    with self._lock:
                        drained_set = self._drained_durable_keys.setdefault(clean_fcid, set())
                        if key_pair in seen_keys or key_pair in drained_set:
                            continue
                        drained_set.add(key_pair)
                        seen_keys.add(key_pair)
                    if reminder_queue.add(
                        "friend_delivery",
                        summary_text,
                        key=f"friend_delivery:{qid}:{st}",
                    ):
                        added += 1
            except Exception:  # noqa: BLE001
                logger.debug("Could not recover durable friend delivery reminders", exc_info=True)
        return added

    def subscribe(self, callback: Callable[[FriendDeliveryNotification], Any]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def recent(self, *, friend_conversation_id: str | None = None) -> list[FriendDeliveryNotification]:
        with self._lock:
            items = list(self._history)
        if friend_conversation_id:
            clean_fcid = str(friend_conversation_id).strip()
            return [item for item in items if item.friend_conversation_id == clean_fcid]
        return items

    def notify(
        self,
        *,
        status: DeliveryWatchStatus,
        user_id: str,
        target_conversation_id: str,
        queue_id: str,
        origin_conversation_id: str | None = None,
        target_turn_id: str | None = None,
        new_turn_id: str | None = None,
        reason: str | None = None,
        store: Any = None,
    ) -> FriendDeliveryNotification | None:
        clean_qid = str(queue_id or "").strip()
        if not clean_qid:
            return None
        dedup_key = (clean_qid, status)
        clean_fcid = str(origin_conversation_id or "").strip() or None
        clean_tcid = str(target_conversation_id or "").strip()
        clean_uid = str(user_id or "").strip()
        summary = _format_summary(status, target_conversation_id=clean_tcid, reason=reason)
        notification = FriendDeliveryNotification(
            status=status,
            user_id=clean_uid,
            friend_conversation_id=clean_fcid,
            target_conversation_id=clean_tcid,
            target_turn_id=str(target_turn_id or "").strip() or None,
            queue_id=clean_qid,
            new_turn_id=str(new_turn_id or "").strip() or None,
            reason=str(reason or "").strip() or None,
            summary=summary,
            created_at=datetime.now(UTC).isoformat(),
        )

        with self._lock:
            if dedup_key in self._emitted_set:
                return None
            if len(self._emitted_keys) == self._emitted_keys.maxlen:
                oldest = self._emitted_keys.popleft()
                self._emitted_set.discard(oldest)
            self._emitted_keys.append(dedup_key)
            self._emitted_set.add(dedup_key)
            self._history.append(notification)
            subscribers = list(self._subscribers)
            reminder_queue = self._reminder_queues.get(clean_fcid) if clean_fcid else None
            watch_cb = self._watch_callbacks.get(clean_fcid) if clean_fcid else None

        # 1) Deliver as a ReminderQueue note to the Friend session (and milestone watcher if attached)
        delivered_queues: set[int] = set()
        if reminder_queue is not None and callable(getattr(reminder_queue, "add", None)):
            try:
                reminder_queue.add(
                    "friend_delivery",
                    notification.summary,
                    key=f"friend_delivery:{clean_qid}:{status}",
                )
                delivered_queues.add(id(reminder_queue))
            except Exception:  # noqa: BLE001
                logger.debug("Failed to push friend delivery reminder to registered queue", exc_info=True)

        try:
            from agentos.conversations.code_milestone_watcher import get_milestone_watcher_registry

            milestone_watcher = get_milestone_watcher_registry().get(clean_tcid, user_id=clean_uid or None)
            mw_queue = getattr(milestone_watcher, "reminder_queue", None) if milestone_watcher is not None else None
            if mw_queue is not None and id(mw_queue) not in delivered_queues and callable(getattr(mw_queue, "add", None)):
                mw_queue.add(
                    "friend_delivery",
                    notification.summary,
                    key=f"friend_delivery:{clean_qid}:{status}",
                )
                delivered_queues.add(id(mw_queue))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to push friend delivery reminder to milestone watcher", exc_info=True)

        if not delivered_queues and clean_fcid:
            with self._lock:
                pending_list = self._pending_reminders.setdefault(clean_fcid, [])
                if not any(p.queue_id == clean_qid and p.status == status for p in pending_list):
                    pending_list.append(notification)
                    if len(pending_list) > 64:
                        del pending_list[:-64]

        # 2) Persist an observable SSE event on the Friend conversation (best-effort)
        engine = getattr(store, "_engine", None) if store is not None else None
        if engine is not None and clean_fcid:
            self._record_friend_conversation_event(store, engine, clean_fcid, notification)
            if status == STATUS_PROMOTED and notification.new_turn_id and clean_uid:
                self._refresh_session_watch(
                    engine,
                    user_id=clean_uid,
                    friend_conversation_id=clean_fcid,
                    target_conversation_id=clean_tcid,
                    new_turn_id=notification.new_turn_id,
                )

        # 3) Notify registered session watch callback if promoted to a new turn
        if status == STATUS_PROMOTED and notification.new_turn_id and callable(watch_cb):
            try:
                watch_cb(
                    user_id=clean_uid,
                    friend_conversation_id=clean_fcid,
                    target_conversation_id=clean_tcid,
                    target_turn_id=notification.new_turn_id,
                    origin="friend",
                )
            except Exception:  # noqa: BLE001
                logger.debug("Failed to invoke watch callback on friend delivery promotion", exc_info=True)

        # 4) Notify in-process subscribers
        for subscriber in subscribers:
            try:
                subscriber(notification)
            except Exception:  # noqa: BLE001
                logger.debug("Friend delivery subscriber raised an exception", exc_info=True)

        return notification

    @staticmethod
    def _record_friend_conversation_event(
        store: Any,
        engine: Any,
        friend_conversation_id: str,
        notification: FriendDeliveryNotification,
    ) -> None:
        if not notification.user_id:
            return
        now = datetime.now(UTC)
        payload = notification.as_payload()

        # Primary: record into conversation_activity_events so SSE (/v1/conversations/{id}/events)
        # and reload snapshots (snapshot_queries.py) deliver friend.delivery_status live to FriendDeliveryCard.
        try:
            friend_turn: dict[str, object] | None = None
            latest_fn = getattr(store, "_latest_turn_for_conversation", None)
            if callable(latest_fn):
                friend_turn = latest_fn(friend_conversation_id, notification.user_id)
            elif engine is not None:
                with engine.connect() as conn:
                    row = (
                        conn.execute(
                            select(conversation_turns)
                            .where(
                                conversation_turns.c.conversation_id == friend_conversation_id,
                                conversation_turns.c.user_id == notification.user_id,
                            )
                            .order_by(conversation_turns.c.created_at.desc())
                            .limit(1)
                        )
                        .mappings()
                        .first()
                    )
                    friend_turn = dict(row) if row is not None else None

            if friend_turn is None:
                synthetic_tid = f"friend-delivery:{friend_conversation_id}"
                friend_turn = {
                    "conversation_id": friend_conversation_id,
                    "user_id": notification.user_id,
                    "turn_id": synthetic_tid,
                    "execution_id": f"exec:{synthetic_tid}",
                }

            activity_fn = getattr(store, "_activity", None)
            if getattr(store, "activity_store", None) is not None and callable(activity_fn):
                activity_fn(
                    friend_turn,
                    AgentActivityEventType.FRIEND_DELIVERY_STATUS,
                    notification.summary,
                    payload,
                )
            elif engine is not None:
                from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore

                act_store = PostgresAgenticActivityStore(engine, "orin-friend-delivery-cursor")
                tid = str(friend_turn["turn_id"])
                exec_id = str(friend_turn.get("execution_id") or f"exec:{tid}")
                agent_id = f"agent:{friend_conversation_id}:main"
                for _ in range(16):
                    with engine.connect() as conn:
                        cur_seq = conn.execute(
                            select(func.max(conversation_activity_events.c.sequence)).where(
                                conversation_activity_events.c.turn_id == tid
                            )
                        ).scalar()
                    seq = int(cur_seq or 0) + 1
                    try:
                        act_store.record_event(
                            AgentActivityEvent(
                                event_id=f"activity:{tid}:{seq}",
                                conversation_id=friend_conversation_id,
                                turn_id=tid,
                                execution_id=exec_id,
                                user_id=notification.user_id,
                                agent_id=agent_id,
                                parent_agent_id=None,
                                event_type=AgentActivityEventType.FRIEND_DELIVERY_STATUS,
                                sequence=seq,
                                summary=notification.summary[:512] or "Entrega do Friend",
                                payload=payload,
                                created_at=now,
                            )
                        )
                        break
                    except ValueError:
                        continue
        except Exception:  # noqa: BLE001
            logger.debug("Could not persist friend.delivery_status activity event", exc_info=True)

        # Secondary audit row in conversation_events (for legacy fallback stores without activity_store).
        try:
            with engine.begin() as conn:
                conn.execute(
                    insert(conversation_events).values(
                        conversation_id=friend_conversation_id,
                        user_id=notification.user_id,
                        event_type="friend.delivery_status",
                        message_id=None,
                        payload=payload,
                        created_at=now,
                    )
                )
        except Exception:  # noqa: BLE001
            logger.debug("Could not persist friend.delivery_status event", exc_info=True)

    @staticmethod
    def _refresh_session_watch(
        engine: Any,
        *,
        user_id: str,
        friend_conversation_id: str,
        target_conversation_id: str,
        new_turn_id: str,
    ) -> None:
        try:
            from agentos.discord.store import PostgresDiscordStore

            discord_store = PostgresDiscordStore(engine)
            cfg = discord_store.get_config(user_id)
            notify_channel = cfg.default_notify_channel_id if cfg is not None else None
            discord_store.upsert_watch(
                user_id,
                target_conversation_id,
                new_turn_id,
                friend_conversation_id=friend_conversation_id,
                origin="friend",
                discord_channel_id=notify_channel,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Could not refresh session watch for promoted friend delivery", exc_info=True)


_GLOBAL_WATCHER = FriendDeliveryWatcherRegistry()


def get_friend_delivery_watcher() -> FriendDeliveryWatcherRegistry:
    return _GLOBAL_WATCHER


def register_friend_delivery_target(
    *,
    friend_conversation_id: str | None,
    reminder_queue: Any = None,
    on_watch_notify: Callable[..., Any] | None = None,
) -> None:
    _GLOBAL_WATCHER.register_target(
        friend_conversation_id=friend_conversation_id,
        reminder_queue=reminder_queue,
        on_watch_notify=on_watch_notify,
    )


def unregister_friend_delivery_target(
    *,
    friend_conversation_id: str | None,
    reminder_queue: Any = None,
) -> None:
    _GLOBAL_WATCHER.unregister_target(
        friend_conversation_id=friend_conversation_id,
        reminder_queue=reminder_queue,
    )


def drain_pending_friend_deliveries(
    *,
    friend_conversation_id: str | None,
    reminder_queue: Any,
    store: Any = None,
    user_id: str | None = None,
) -> int:
    return _GLOBAL_WATCHER.drain_pending_for_friend(
        friend_conversation_id,
        reminder_queue,
        store=store,
        user_id=user_id,
    )


def notify_friend_delivery_injected(
    *,
    user_id: str,
    target_conversation_id: str,
    queue_id: str,
    origin_conversation_id: str | None = None,
    target_turn_id: str | None = None,
    store: Any = None,
) -> FriendDeliveryNotification | None:
    return _GLOBAL_WATCHER.notify(
        status=STATUS_INJECTED,
        user_id=user_id,
        target_conversation_id=target_conversation_id,
        queue_id=queue_id,
        origin_conversation_id=origin_conversation_id,
        target_turn_id=target_turn_id,
        store=store,
    )


def notify_friend_delivery_promoted(
    *,
    user_id: str,
    target_conversation_id: str,
    queue_id: str,
    origin_conversation_id: str | None = None,
    target_turn_id: str | None = None,
    new_turn_id: str | None = None,
    store: Any = None,
) -> FriendDeliveryNotification | None:
    return _GLOBAL_WATCHER.notify(
        status=STATUS_PROMOTED,
        user_id=user_id,
        target_conversation_id=target_conversation_id,
        queue_id=queue_id,
        origin_conversation_id=origin_conversation_id,
        target_turn_id=target_turn_id,
        new_turn_id=new_turn_id,
        store=store,
    )


def notify_friend_delivery_blocked(
    *,
    user_id: str,
    target_conversation_id: str,
    queue_id: str,
    origin_conversation_id: str | None = None,
    target_turn_id: str | None = None,
    reason: str | None = None,
    store: Any = None,
) -> FriendDeliveryNotification | None:
    return _GLOBAL_WATCHER.notify(
        status=STATUS_BLOCKED,
        user_id=user_id,
        target_conversation_id=target_conversation_id,
        queue_id=queue_id,
        origin_conversation_id=origin_conversation_id,
        target_turn_id=target_turn_id,
        reason=reason,
        store=store,
    )


__all__ = [
    "DeliveryWatchStatus",
    "FriendDeliveryNotification",
    "FriendDeliveryWatcherRegistry",
    "STATUS_BLOCKED",
    "STATUS_INJECTED",
    "STATUS_PROMOTED",
    "drain_pending_friend_deliveries",
    "get_friend_delivery_watcher",
    "notify_friend_delivery_blocked",
    "notify_friend_delivery_injected",
    "notify_friend_delivery_promoted",
    "register_friend_delivery_target",
    "unregister_friend_delivery_target",
]
