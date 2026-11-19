from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from agentos.agentic.events import AgentActivityEventType
from agentos.api.contracts import ApplicationNotFoundError
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import metadata


def test_long_friend_conversation_paginates_messages_and_header_lookup() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret")
    store = PostgresChatStore(engine, activity_store)

    friend = store.ensure_friend_conversation("user-1")
    cid = str(friend["conversation_id"])

    # Populate 65 full turns = 130 messages + multi-token deltas
    for idx in range(65):
        receipt = store.create(
            conversation_id=cid,
            user_id="user-1",
            message=f"Mensagem do usuário #{idx}",
            provider="openrouter",
            model_id="anthropic/claude-sonnet-4.5",
            idempotency_key=f"friend-turn-{idx}",
        )
        turn = store.claim(receipt.turn_id)
        assert turn is not None
        for part in (f"Resposta #{idx} ", "parte A ", "parte B."):
            store.delta(turn, part)
        store.finish(turn)

    header = store.get_header(cid, "user-1")
    assert header["conversation_id"] == cid
    assert header["mode"] == "friend"
    with pytest.raises(ApplicationNotFoundError):
        store.get_header(cid, "other-user")

    # Windowed snapshot returns only the latest 40 messages in ascending sequence order
    snap = store.get(cid, "user-1", message_limit=40)
    assert len(snap["messages"]) == 40
    assert snap["has_older_messages"] is True
    assert isinstance(snap["oldest_message_sequence"], int)
    assert snap["messages"][-1]["content"] == "Resposta #64 parte A parte B."
    assert snap["messages"][0]["sequence"] == snap["oldest_message_sequence"]

    # Walk backwards through older message pages until reaching the start without gaps or duplicates
    collected = list(snap["messages"])
    cursor_seq = snap["oldest_message_sequence"]
    has_older = snap["has_older_messages"]
    pages = 0
    while has_older and cursor_seq is not None:
        pages += 1
        assert pages <= 10
        page = store.list_older_messages(cid, "user-1", before_sequence=cursor_seq, limit=40)
        assert len(page["messages"]) > 0
        assert len(page["turns"]) > 0
        collected = list(page["messages"]) + collected
        has_older = bool(page["has_older_messages"])
        cursor_seq = page["oldest_message_sequence"]

    assert len(collected) == 130
    sequences = [int(m["sequence"]) for m in collected]
    assert sequences == sorted(set(sequences))
    assert collected[0]["content"] == "Mensagem do usuário #0"
    assert collected[-1]["content"] == "Resposta #64 parte A parte B."

    # Unbounded Python store.get() remains backwards-compatible
    full = store.get(cid, "user-1")
    assert len(full["messages"]) == 130
    assert full["has_older_messages"] is False
    assert full["oldest_message_sequence"] is None

    # ChatApplication delegates get_header, message_limit, and list_older_messages
    app = ChatApplication(store=store)
    assert app.get_header(cid, "user-1")["conversation_id"] == cid
    app_snap = app.get(cid, "user-1", message_limit=20)
    assert len(app_snap["messages"]) == 20
    assert app_snap["has_older_messages"] is True
    older_via_app = app.list_older_messages(
        cid, "user-1", before_sequence=int(app_snap["oldest_message_sequence"]), limit=20
    )
    assert len(older_via_app["messages"]) == 20
    assert older_via_app["has_older_messages"] is True


def test_windowed_snapshot_bounds_activity_scan_to_visible_turns_without_losing_older_cursor() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret")
    store = PostgresChatStore(engine, activity_store)

    friend = store.ensure_friend_conversation("user-1")
    cid = str(friend["conversation_id"])

    for idx in range(30):
        receipt = store.create(
            conversation_id=cid,
            user_id="user-1",
            message=f"Pergunta {idx}",
            provider="openrouter",
            model_id="anthropic/claude-sonnet-4.5",
            idempotency_key=f"act-turn-{idx}",
        )
        turn = store.claim(receipt.turn_id)
        assert turn is not None
        store.record(
            turn,
            AgentActivityEventType.FILESYSTEM_READ,
            f"Leu nota_{idx}.md",
            {"path": f"nota_{idx}.md", "tool_name": "read_file", "tool_kind": "filesystem"},
        )
        store.delta(turn, f"Resposta {idx}")
        store.finish(turn)

    # Request only the last 10 messages (5 turns)
    snap = store.get(cid, "user-1", message_limit=10)
    assert len(snap["messages"]) == 10
    assert snap["has_older_messages"] is True
    assert snap["has_older_activities"] is True
    assert isinstance(snap["oldest_activity_id"], int)

    visible_turn_ids = {m["turn_id"] for m in snap["messages"] if m.get("turn_id")}
    activity_turn_ids = {a["turn_id"] for a in snap["activities"] if a.get("turn_id")}
    assert activity_turn_ids.issubset(visible_turn_ids)

    # Fetching older activities continues cleanly right before oldest_activity_id
    older_acts = store.list_older_activities(
        cid, "user-1", before_id=int(snap["oldest_activity_id"]), limit=150
    )
    assert len(older_acts["activities"]) > 0
    summaries = {item["summary"] for item in older_acts["activities"]}
    assert any(s.startswith("Leu nota_") for s in summaries)
