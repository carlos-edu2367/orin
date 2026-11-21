"""WS-B7: Real delivery ack (queued -> injected -> delivered), idempotency, queue full -> forwarded, Reminder.key."""
from __future__ import annotations

import threading
from typing import Any

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.message_ack import (
    MessageAcknowledger,
    USER_MESSAGE_KEY_PREFIX,
    ack_delivered_reminders,
    control_id_from_reminder_key,
)
from agentos.agentic.reminders import Reminder, ReminderQueue
from agentos.agentic.session import _AgentControlWatcher
from agentos.conversations.agent_controls import InMemoryAgentControls


def test_reminder_preserves_key_on_add_replace_and_drain() -> None:
    q = ReminderQueue()
    assert q.add("note", "plain") is True
    assert q.add("user_message", "body", key="user-msg:ctrl_abc") is True
    assert q.replace("user-msg:ctrl_abc", "user_message", "updated body") is True

    drained = q.drain()
    assert drained == [
        Reminder("note", "plain", key=""),
        Reminder("user_message", "updated body", key="user-msg:ctrl_abc"),
    ]
    assert control_id_from_reminder_key(drained[0].key) is None
    assert control_id_from_reminder_key(drained[1].key) == "ctrl_abc"


def test_message_acknowledger_is_idempotent_and_emits_once() -> None:
    store = InMemoryAgentControls()
    cid = store.post_message("u1", "c1", "a1", "oi", sender_kind="parent", sender_agent_id="main")
    store.take("u1", "c1", "a1")
    store.set_outcome(cid, "injected")

    delivered_events: list[tuple[str, Any]] = []
    acker = MessageAcknowledger(
        store,
        on_delivered=lambda control_id, delivered_at: delivered_events.append((control_id, delivered_at)),
    )

    assert acker.ack(cid) is True
    assert acker.ack(cid) is False  # idempotent
    assert len(delivered_events) == 1
    assert delivered_events[0][0] == cid
    assert delivered_events[0][1] is not None

    items = store.list_for_agent("u1", "c1", "a1")
    assert items[0]["state"] == "delivered"
    assert items[0]["delivered_at"] == delivered_events[0][1]


def test_ack_delivered_reminders_extracts_user_msg_keys_only() -> None:
    seen: list[str] = []
    items = [
        Reminder("note", "x", key=""),
        Reminder("user_message", "a", key=f"{USER_MESSAGE_KEY_PREFIX}ctrl_1"),
        Reminder("plan_mode", "p", key="plan_mode:1"),
        Reminder("user_message", "b", key=f"{USER_MESSAGE_KEY_PREFIX}ctrl_2"),
    ]
    acked = ack_delivered_reminders(items, seen.append)
    assert acked == ["ctrl_1", "ctrl_2"]
    assert seen == ["ctrl_1", "ctrl_2"]


def test_watcher_marks_injected_then_delivered_on_ack_and_forwards_if_unacked() -> None:
    store = InMemoryAgentControls()
    child_reminders = ReminderQueue()
    main_reminders = ReminderQueue()
    events: list[tuple[AgentActivityEventType, str, dict[str, Any]]] = []
    clock_val = [10.0]

    watcher = _AgentControlWatcher(
        store=store,
        user_id="u1",
        conversation_id="c1",
        agent_id="agent-child",
        clean="worker",
        event=threading.Event(),
        reminders=child_reminders,
        main_reminders=main_reminders,
        record_activity=lambda et, summary, payload, **_kw: events.append((et, summary, dict(payload or {}))),
        clock=lambda: clock_val[0],
        sender_name_resolver=lambda aid: "Orin" if aid == "main" else aid,
    )

    c1 = store.post_message("u1", "c1", "agent-child", "msg entregue", sender_kind="user")
    c2 = store.post_message(
        "u1", "c1", "agent-child", "msg nao lida", sender_kind="parent", sender_agent_id="main"
    )

    watcher.poll()
    # Both must be `injected` right after poll, NEVER `delivered`!
    listed = {item["control_id"]: item for item in store.list_for_agent("u1", "c1", "agent-child")}
    assert listed[c1]["state"] == "injected"
    assert listed[c1]["delivered_at"] is None
    assert listed[c2]["state"] == "injected"
    assert listed[c2]["delivered_at"] is None

    # Check reminder headings distinguish user vs parent.
    drained = child_reminders.drain()
    assert len(drained) == 2
    assert drained[0].key == f"user-msg:{c1}"
    assert "Message from the user (sent to you directly while you work):" in drained[0].text
    assert drained[1].key == f"user-msg:{c2}"
    assert "Message from parent agent Orin (sent to you directly while you work):" in drained[1].text

    # Simulate only c1 being acked (e.g. c2 was put back or never acked).
    watcher.ack_control(c1)
    listed_mid = {item["control_id"]: item for item in store.list_for_agent("u1", "c1", "agent-child")}
    assert listed_mid[c1]["state"] == "delivered"
    assert listed_mid[c1]["delivered_at"] is not None
    assert listed_mid[c2]["state"] == "injected"

    # Re-add c2 to simulate it remained unread in queue when subagent finished.
    watcher.finalize()
    listed_end = {item["control_id"]: item for item in store.list_for_agent("u1", "c1", "agent-child")}
    assert listed_end[c1]["state"] == "delivered"
    assert listed_end[c2]["state"] == "forwarded"
    assert any("msg nao lida" in r.text for r in main_reminders.drain())

    event_types = [ev[0] for ev in events]
    assert AgentActivityEventType.AGENT_MESSAGE_DELIVERED in event_types
    assert AgentActivityEventType.AGENT_MESSAGE_FORWARDED in event_types


def test_watcher_forwards_when_reminder_queue_is_full() -> None:
    store = InMemoryAgentControls()
    child_reminders = ReminderQueue(max_items=1)
    child_reminders.add("note", "already full")
    main_reminders = ReminderQueue()
    events: list[tuple[AgentActivityEventType, dict[str, Any]]] = []

    watcher = _AgentControlWatcher(
        store=store,
        user_id="u1",
        conversation_id="c1",
        agent_id="agent-full",
        clean="worker",
        event=threading.Event(),
        reminders=child_reminders,
        main_reminders=main_reminders,
        record_activity=lambda et, _summary, payload, **_kw: events.append((et, dict(payload or {}))),
    )

    cid = store.post_message("u1", "c1", "agent-full", "fila cheia")
    watcher.poll()

    listed = store.list_for_agent("u1", "c1", "agent-full")
    assert listed[0]["state"] == "forwarded"
    assert any("fila cheia" in r.text for r in main_reminders.drain())
    assert any(
        et == AgentActivityEventType.AGENT_MESSAGE_FORWARDED and payload.get("control_id") == cid
        for et, payload in events
    )
