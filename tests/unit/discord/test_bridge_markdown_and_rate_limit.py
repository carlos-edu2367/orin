from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentos.api.contracts import ApplicationNotFoundError
from agentos.discord.bridge_service import DiscordBridgeService
from agentos.discord.rest_client import DiscordApiError, DiscordRestClient
from agentos.discord.store import FriendSessionWatch

CHAN_ID = "123456789012345678"
PROG_MSG_ID = "1571375025511071885"
PROMPT_MSG_ID = "1571375025511071886"


@pytest.mark.anyio
async def test_rest_client_fails_fast_when_retry_after_exceeds_sleep_cap() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 120.0})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DiscordRestClient(
        "bot-token",
        client=http_client,
        sleep_fn=fake_sleep,
    )
    try:
        with pytest.raises(DiscordApiError) as exc_info:
            await client.edit_message(CHAN_ID, PROG_MSG_ID, content="hi")
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 120.0
        # Must NOT waste 3 x 10s sleeping when retry_after > MAX_RATE_LIMIT_SLEEP_SECONDS
        assert slept == []
    finally:
        await client.aclose()
        await http_client.aclose()


@pytest.mark.anyio
async def test_rest_client_retries_short_429_and_surfaces_retry_after_on_exhaustion() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Rate limited", "retry_after": 1.5})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DiscordRestClient(
        "bot-token",
        client=http_client,
        sleep_fn=fake_sleep,
    )
    try:
        with pytest.raises(DiscordApiError) as exc_info:
            await client.send_message(CHAN_ID, content="hello")
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 1.5
        assert slept == [1.5, 1.5, 1.5]
    finally:
        await client.aclose()
        await http_client.aclose()


@pytest.mark.anyio
async def test_watch_coordinator_backoff_zombies_and_turn_advance() -> None:
    now_ref = [datetime(2026, 11, 15, 15, 0, tzinfo=UTC)]
    discord_store = MagicMock()
    updated_calls: list[tuple[str, dict]] = []

    def record_update(watch_id: str, **kwargs):
        assert "target_turn_id" not in kwargs, "update_watch forbids target_turn_id"
        updated_calls.append((watch_id, kwargs))
        return None

    discord_store.update_watch.side_effect = record_update
    discord_store.get_config.return_value = MagicMock(
        default_notify_channel_id=CHAN_ID,
        allowed_channel_ids=(CHAN_ID,),
    )
    discord_store.list_open_watches.return_value = []

    chat_store = MagicMock()
    chat_store.get.return_value = {
        "conversation_id": "conv_run",
        "title": "Marketfy",
        "mode": "code",
        "state": "running",
        "turns": [{"turn_id": "turn_1", "state": "running"}],
        "messages": [{"role": "assistant", "content": "[partial](workspace://a/b/c"}],
        "activities": [],
    }
    rest = MagicMock()
    rest.edit_message = AsyncMock(
        side_effect=DiscordApiError("rate limit", status_code=429, retry_after=90.0)
    )
    rest.send_message = AsyncMock(
        side_effect=DiscordApiError("unknown channel", status_code=404, error_code=10003)
    )

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=chat_store,
        rest_client_factory=lambda _t: rest,
        clock=lambda: now_ref[0],
    )
    bridge._get_rest_client = lambda _u: rest  # type: ignore[method-assign]
    bridge._ensure_stop_binding = AsyncMock(return_value="bind_1")  # type: ignore[method-assign]

    watch = FriendSessionWatch(
        watch_id="fsw_rate",
        user_id="local-user",
        friend_conversation_id="friend_1",
        target_conversation_id="conv_run",
        target_turn_id="turn_1",
        origin="friend",
        discord_channel_id=CHAN_ID,
        discord_progress_message_id=PROG_MSG_ID,
        discord_prompt_message_id=None,
        status="watching",
        last_activity_seq=1,
        last_digest_hash="old_hash",
        last_prompt_fingerprint=None,
        last_patched_at=now_ref[0] - timedelta(seconds=60),
        notified_terminal=False,
        created_at=now_ref[0] - timedelta(hours=3),
        updated_at=now_ref[0] - timedelta(seconds=60),
    )

    # 1. During `running`, mid-stream artifacts must NOT be extracted
    insp = bridge._inspect_watched_session(watch)
    assert insp is not None
    assert insp["snapshot"]["artifacts"] == []

    # 2. 429 on PATCH sets >=120s backoff (card > 45min old) and updates last_patched_at
    await bridge.watch_coordinator._process_watch(watch)
    assert rest.edit_message.await_count == 1
    assert any("last_patched_at" in kw for _, kw in updated_calls)
    assert bridge.watch_coordinator._patch_backoff_until["fsw_rate"] >= now_ref[0].timestamp() + 120.0

    # Immediate next tick 2s later must respect backoff (0 new PATCH calls, 0 stop_binding calls!)
    bridge._ensure_stop_binding.reset_mock()
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(watch)
    assert rest.edit_message.await_count == 1
    assert bridge._ensure_stop_binding.await_count == 0

    # 3a. Unknown Channel (404 code 10003) on send_message applies 300s backoff instead of 2s spam
    no_card_watch = replace(watch, watch_id="fsw_no_chan", discord_progress_message_id=None)
    await bridge.watch_coordinator._process_watch(no_card_watch)
    assert rest.send_message.await_count == 1
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(no_card_watch)
    assert rest.send_message.await_count == 1

    # 3b. 404 Unknown Message (code 10008) on edit_message clears discord_progress_message_id with 15s cooldown,
    # while 403 Forbidden applies 300s channel cooldown without clearing discord_progress_message_id
    updated_calls.clear()
    rest.edit_message.side_effect = DiscordApiError("unknown message", status_code=404, error_code=10008)
    msg_404_watch = replace(watch, watch_id="fsw_msg_404")
    await bridge.watch_coordinator._process_watch(msg_404_watch)
    assert any(
        wid == "fsw_msg_404" and kw.get("discord_progress_message_id") is None and "last_patched_at" in kw
        for wid, kw in updated_calls
    )
    # 2s later after clearing progress_msg_id, 15s cooldown prevents immediate send_message spam
    msg_404_cleared = replace(msg_404_watch, discord_progress_message_id=None)
    rest.send_message.reset_mock()
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(msg_404_cleared)
    assert rest.send_message.await_count == 0

    updated_calls.clear()
    rest.edit_message.side_effect = DiscordApiError("missing permissions", status_code=403, error_code=50013)
    forbidden_watch = replace(watch, watch_id="fsw_403")
    await bridge.watch_coordinator._process_watch(forbidden_watch)
    assert bridge.watch_coordinator._patch_backoff_until["fsw_403"] == pytest.approx(
        now_ref[0].timestamp() + 300.0
    )
    assert not any(
        wid == "fsw_403" and "discord_progress_message_id" in kw
        for wid, kw in updated_calls
    )

    # 4. Cancelled turn that previously had an ask_user question MUST close as terminal
    chat_store.get.return_value = {
        "conversation_id": "conv_cancel",
        "title": "Cancelled Session",
        "mode": "code",
        "state": "cancelled",
        "turns": [{"turn_id": "turn_cancel", "state": "cancelled"}],
        "messages": [],
        "activities": [
            {
                "id": 10,
                "turn_id": "turn_cancel",
                "event_type": "turn.waiting_user",
                "summary": "Pergunta",
                "payload": {"in_turn": True, "questions": [{"question": "Continuar?"}]},
            }
        ],
    }
    cancelled_watch = FriendSessionWatch(
        watch_id="fsw_zombie",
        user_id="local-user",
        friend_conversation_id=None,
        target_conversation_id="conv_cancel",
        target_turn_id="turn_cancel",
        origin="code_auto",
        discord_channel_id=CHAN_ID,
        discord_progress_message_id=None,
        discord_prompt_message_id=PROMPT_MSG_ID,
        status="waiting_user",
        last_activity_seq=10,
        last_digest_hash=None,
        last_prompt_fingerprint="fp1",
        last_patched_at=None,
        notified_terminal=False,
        created_at=now_ref[0] - timedelta(hours=5),
        updated_at=now_ref[0] - timedelta(hours=5),
    )
    updated_calls.clear()
    await bridge.watch_coordinator._process_watch(cancelled_watch)
    assert any(
        wid == "fsw_zombie" and kw.get("status") == "cancelled" and kw.get("notified_terminal") is True
        for wid, kw in updated_calls
    )

    # 5a. Transient DB error (RuntimeError) returns None and does NOT close watch
    chat_store.get.side_effect = RuntimeError("database is locked")
    transient_watch = replace(cancelled_watch, watch_id="fsw_transient")
    updated_calls.clear()
    await bridge.watch_coordinator._process_watch(transient_watch)
    assert updated_calls == []

    # 5b. Deleted conversation (`ApplicationNotFoundError`) closes watch instead of looping forever
    chat_store.get.side_effect = ApplicationNotFoundError("conversation not found")
    deleted_watch = replace(cancelled_watch, watch_id="fsw_deleted")
    updated_calls.clear()
    await bridge.watch_coordinator._process_watch(deleted_watch)
    assert any(
        wid == "fsw_deleted" and kw.get("status") == "cancelled" and kw.get("notified_terminal") is True
        for wid, kw in updated_calls
    )

    # 6. Advancing to a newer active turn calls `_advance_watch_to_new_turn` preserving all origins ("friend", "discord", "code_auto")
    chat_store.get.side_effect = None
    chat_store.get.return_value = {
        "conversation_id": "conv_run",
        "title": "Marketfy",
        "mode": "code",
        "state": "running",
        "turns": [
            {"turn_id": "turn_1", "state": "cancelled"},
            {"turn_id": "turn_2", "state": "running"},
        ],
        "messages": [],
        "activities": [],
    }
    for expected_origin in ("friend", "discord", "code_auto"):
        discord_store.upsert_watch.reset_mock()
        origin_watch = replace(watch, origin=expected_origin)
        discord_store.list_open_watches.return_value = [origin_watch]
        discord_store.upsert_watch.return_value = replace(origin_watch, target_turn_id="turn_2")
        await bridge.watch_coordinator._process_watch(origin_watch)
        discord_store.upsert_watch.assert_called_once()
        assert discord_store.upsert_watch.call_args.kwargs["origin"] == expected_origin

    # 7. `_inspect_conversation_by_id(..., conv=...)` performs 0 extra `chat_store.get` calls,
    # and `_await_turn_with_typing` uses 0.45s default poll interval and 1 SQLite read per tick
    assert bridge._turn_poll_interval == pytest.approx(0.45)
    chat_store.get.reset_mock()
    snap = bridge._inspect_conversation_by_id("local-user", "conv_run", conv=chat_store.get.return_value)
    assert snap["conversation_id"] == "conv_run"
    assert chat_store.get.call_count == 0

    tick_snapshots = [
        {
            "conversation_id": "conv_typing",
            "title": "Orin Friend",
            "mode": "chat",
            "state": "running",
            "turns": [{"turn_id": "turn_t1", "state": "running"}],
            "messages": [],
            "activities": [],
        },
        {
            "conversation_id": "conv_typing",
            "title": "Orin Friend",
            "mode": "chat",
            "state": "completed",
            "turns": [{"turn_id": "turn_t1", "state": "completed"}],
            "messages": [{"role": "assistant", "content": "Pronto!"}],
            "activities": [],
        },
    ]
    chat_store.get.reset_mock()
    chat_store.get.side_effect = tick_snapshots
    rest.trigger_typing = AsyncMock(return_value=None)
    bridge._turn_poll_interval = 0.02
    final_conv = await bridge._await_turn_with_typing(
        user_id="local-user",
        conversation_id="conv_typing",
        turn_id="turn_t1",
        channel_id=CHAN_ID,
        rest_client=rest,
    )
    assert final_conv["state"] == "completed"
    # Tick 1 (running) + Tick 2 (completed) == exactly 2 calls (1 per tick, never 3 per tick!)
    assert chat_store.get.call_count == 2

    # 8. `waiting_user` Decision Card respects 429/403/10003 prompt backoff without leaking bindings every 2s
    chat_store.get.side_effect = None
    chat_store.get.return_value = {
        "conversation_id": "conv_wait",
        "title": "Blocked Code Session",
        "mode": "code",
        "state": "waiting_user",
        "turns": [{"turn_id": "turn_wait", "state": "waiting_user"}],
        "messages": [],
        "activities": [
            {
                "id": 20,
                "turn_id": "turn_wait",
                "event_type": "turn.waiting_user",
                "summary": "Escolher",
                "payload": {
                    "in_turn": True,
                    "questions": [{"header": "Q1", "question": "Confirmar?", "options": [{"label": "Sim", "value": "sim"}]}],
                },
            }
        ],
    }
    discord_store.create_binding.reset_mock()
    discord_store.create_binding.side_effect = lambda *args, **kwargs: MagicMock(binding_id="dbnd_prompt_1")
    rest.send_message.reset_mock()
    rest.send_message.side_effect = DiscordApiError("unknown channel", status_code=404, error_code=10003)

    waiting_auto_watch = FriendSessionWatch(
        watch_id="fsw_wait_backoff",
        user_id="local-user",
        friend_conversation_id=None,
        target_conversation_id="conv_wait",
        target_turn_id="turn_wait",
        origin="code_auto",
        discord_channel_id=CHAN_ID,
        discord_progress_message_id=None,
        discord_prompt_message_id=None,
        status="watching",
        last_activity_seq=0,
        last_digest_hash=None,
        last_prompt_fingerprint=None,
        last_patched_at=None,
        notified_terminal=False,
        created_at=now_ref[0] - timedelta(minutes=10),
        updated_at=now_ref[0] - timedelta(minutes=10),
    )
    await bridge.watch_coordinator._process_watch(waiting_auto_watch)
    assert rest.send_message.await_count == 1
    initial_binding_calls = discord_store.create_binding.call_count
    assert initial_binding_calls >= 1

    # Next ticks 2s and 4s later must NOT call send_message or create_binding again during 300s backoff
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(waiting_auto_watch)
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(waiting_auto_watch)
    assert rest.send_message.await_count == 1
    assert discord_store.create_binding.call_count == initial_binding_calls

    # 9. Rewound / deleted `watch.target_turn_id` with historical completed `turn_1` closes watch as cancelled
    # without advancing backward onto `turn_1` or waking Friend
    chat_store.get.return_value = {
        "conversation_id": "conv_rewound",
        "title": "Rewound Session",
        "mode": "code",
        "state": "completed",
        "turns": [{"turn_id": "turn_1", "state": "completed"}],
        "messages": [{"role": "assistant", "content": "Resposta antiga do turn_1"}],
        "activities": [],
    }
    discord_store.upsert_watch.reset_mock()
    updated_calls.clear()
    rest.edit_message.reset_mock()
    rest.edit_message.side_effect = None
    rest.edit_message.return_value = {"id": PROG_MSG_ID}
    rewound_watch = FriendSessionWatch(
        watch_id="fsw_rewound",
        user_id="local-user",
        friend_conversation_id="friend_1",
        target_conversation_id="conv_rewound",
        target_turn_id="turn_2",
        origin="friend",
        discord_channel_id=CHAN_ID,
        discord_progress_message_id=PROG_MSG_ID,
        discord_prompt_message_id=None,
        status="watching",
        last_activity_seq=5,
        last_digest_hash="old_hash",
        last_prompt_fingerprint=None,
        last_patched_at=now_ref[0] - timedelta(seconds=30),
        notified_terminal=False,
        created_at=now_ref[0] - timedelta(minutes=5),
        updated_at=now_ref[0] - timedelta(seconds=30),
    )
    await bridge.watch_coordinator._process_watch(rewound_watch)
    discord_store.upsert_watch.assert_not_called()
    assert any(
        wid == "fsw_rewound" and kw.get("status") == "cancelled" and kw.get("notified_terminal") is True
        for wid, kw in updated_calls
    )
    assert rest.edit_message.await_count == 1
    assert rest.edit_message.call_args.kwargs["components"] == []

