"""Unit tests for DiscordGatewayClient, DiscordBridgeService, and SessionWatchCoordinator."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.events import AgentActivityEventType
from agentos.code_mode.permissions import ToolPermissionBroker
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.discord import (
    DEFAULT_ATTACHMENT_ONLY_PROMPT,
    DiscordBridgeService,
    DiscordFileAttachment,
    DiscordGatewayClient,
    DiscordRuntimeStatus,
    OP_DISPATCH,
    OP_HEARTBEAT,
    OP_HEARTBEAT_ACK,
    OP_HELLO,
    OP_IDENTIFY,
    OP_RECONNECT,
    OP_RESUME,
    PostgresDiscordStore,
)
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.projects import PostgresProjectStore
from agentos.uploads.staging import UploadStaging


def _make_cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher("test-master-secret-key-for-discord-bridge-0060")


class _FakeWebSocket:
    """Scripted async WebSocket connection for deterministic Gateway v10 handshake tests."""

    def __init__(
        self,
        incoming_packets: list[dict[str, Any]],
        *,
        close_code_on_exit: int | None = None,
        on_send: Any = None,
    ) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        for pkt in incoming_packets:
            self._queue.put_nowait(json.dumps(pkt))
        self.sent_packets: list[dict[str, Any]] = []
        self.close_code: int | None = close_code_on_exit
        self.closed = False
        self._on_send = on_send

    def push_packet(self, pkt: dict[str, Any]) -> None:
        self._queue.put_nowait(json.dumps(pkt))

    def finish(self, close_code: int | None = None) -> None:
        if close_code is not None:
            self.close_code = close_code
        self._queue.put_nowait(None)

    async def send(self, data: str) -> None:
        parsed = json.loads(data)
        self.sent_packets.append(parsed)
        if callable(self._on_send):
            await self._on_send(self, parsed)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self._queue.put_nowait(None)

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeWsConnectContext:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWebSocket:
        return self._ws

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeRestClient:
    """In-memory spy for DiscordRestClient used in DiscordBridgeService unit tests."""

    def __init__(self, bot_token: str = "fake_token") -> None:
        self.bot_token = bot_token
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.acked_interactions: list[dict[str, Any]] = []
        self.edited_interaction_originals: list[dict[str, Any]] = []
        self.typing_channels: list[str] = []
        self.attachments_by_url: dict[str, bytes] = {
            "https://cdn.discordapp.com/attachments/111/222/erro.png": (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf"
                b"\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
            )
        }
        self._msg_seq = 100000000000000000
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def trigger_typing(self, channel_id: str) -> None:
        self.typing_channels.append(channel_id)

    async def send_message(
        self,
        channel_id: str,
        *,
        content: str | None = None,
        embeds: Any = None,
        components: Any = None,
        files: Any = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        self._msg_seq += 1
        msg_id = str(self._msg_seq)
        record = {
            "id": msg_id,
            "channel_id": channel_id,
            "content": content,
            "embeds": list(embeds) if embeds else [],
            "components": list(components) if components else [],
            "files": list(files) if files else [],
            "reply_to_message_id": reply_to_message_id,
        }
        self.sent_messages.append(record)
        return record

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        content: str | None = None,
        embeds: Any = None,
        components: Any = None,
    ) -> dict[str, Any]:
        record = {
            "id": message_id,
            "channel_id": channel_id,
            "content": content,
            "embeds": list(embeds) if embeds else [],
            "components": list(components) if components else [],
        }
        self.edited_messages.append(record)
        return record

    async def ack_interaction(
        self,
        interaction_id: str,
        interaction_token: str,
        *,
        response_type: int = 6,
        data: Any = None,
    ) -> None:
        self.acked_interactions.append(
            {
                "interaction_id": interaction_id,
                "interaction_token": interaction_token,
                "response_type": response_type,
                "data": dict(data) if isinstance(data, dict) else data,
            }
        )

    async def edit_interaction_original(
        self,
        application_id: str,
        interaction_token: str,
        *,
        content: str | None = None,
        embeds: Any = None,
        components: Any = None,
    ) -> dict[str, Any]:
        record = {
            "application_id": application_id,
            "interaction_token": interaction_token,
            "content": content,
            "embeds": list(embeds) if embeds else [],
            "components": list(components) if components else [],
        }
        self.edited_interaction_originals.append(record)
        return record

    async def download_attachment(self, url: str, *, max_bytes: int = 25 * 1024 * 1024) -> bytes:
        from agentos.discord.rest_client import DiscordAttachmentError, validate_discord_attachment_url

        validate_discord_attachment_url(url)
        data = self.attachments_by_url.get(url, b"default-attachment-content")
        if len(data) > max_bytes:
            raise DiscordAttachmentError("Anexo excede max_bytes")
        return data


class _FakeGatewayClient:
    """In-memory stub for DiscordGatewayClient when testing DiscordBridgeService lifecycle."""

    def __init__(
        self,
        bot_token: str,
        *,
        on_message: Any = None,
        on_interaction: Any = None,
        on_ready: Any = None,
        on_status_change: Any = None,
        gateway_url: str = "",
        **_: Any,
    ) -> None:
        self.bot_token = bot_token
        self.on_message = on_message
        self.on_interaction = on_interaction
        self.on_ready = on_ready
        self.on_status_change = on_status_change
        self.gateway_url = gateway_url
        self.started = False
        self.stopped = False
        self._status = DiscordRuntimeStatus(
            status="connected",
            last_connected_at=datetime.now(UTC),
            bot_user_id="999888777666555444",
            bot_username="OrinFriendBot",
            bot_application_id="111222333444555666",
        )

    @property
    def status_snapshot(self) -> DiscordRuntimeStatus:
        return self._status

    def start(self) -> asyncio.Task[None]:
        self.started = True

        async def _noop() -> None:
            if callable(self.on_status_change):
                await self.on_status_change(self._status)

        return asyncio.create_task(_noop())

    async def stop(self) -> None:
        self.stopped = True
        self._status = DiscordRuntimeStatus(status="disconnected")


# ----------------------------------------------------------------------
# 1. Gateway Handshake, Heartbeat/ACK, Resume, and Fatal Close Codes
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_gateway_handshake_identify_heartbeat_and_resume() -> None:
    connected_urls: list[str] = []
    ready_events: list[dict[str, Any]] = []
    messages_received: list[dict[str, Any]] = []
    client_ref: list[DiscordGatewayClient] = []

    async def on_send_conn1(ws: _FakeWebSocket, pkt: dict[str, Any]) -> None:
        if pkt["op"] == OP_IDENTIFY:
            ws.push_packet(
                {
                    "op": OP_DISPATCH,
                    "t": "READY",
                    "s": 1,
                    "d": {
                        "session_id": "sess_abc_123",
                        "resume_gateway_url": "wss://us-east1.gateway.discord.gg",
                        "user": {"id": "999888777666555444", "username": "OrinBot"},
                        "application": {"id": "111222333444555666"},
                    },
                }
            )
            # Server requests an immediate Heartbeat (Op 1)
            ws.push_packet({"op": OP_HEARTBEAT, "d": None})
        elif pkt["op"] == OP_HEARTBEAT:
            ws.push_packet({"op": OP_HEARTBEAT_ACK, "d": None})
            # Server requests Op 7 Reconnect to trigger session Resume on connection 2
            ws.push_packet({"op": OP_RECONNECT, "d": None})

    async def on_send_conn2(ws: _FakeWebSocket, pkt: dict[str, Any]) -> None:
        if pkt["op"] == OP_RESUME:
            ws.push_packet({"op": OP_DISPATCH, "t": "RESUMED", "s": 2, "d": {}})
            ws.push_packet(
                {
                    "op": OP_DISPATCH,
                    "t": "MESSAGE_CREATE",
                    "s": 3,
                    "d": {"id": "msg_1", "content": "Oi Orin!"},
                }
            )
            ws.finish()

    ws1 = _FakeWebSocket(
        [{"op": OP_HELLO, "d": {"heartbeat_interval": 60000}}],
        on_send=on_send_conn1,
    )
    ws2 = _FakeWebSocket(
        [{"op": OP_HELLO, "d": {"heartbeat_interval": 60000}}],
        on_send=on_send_conn2,
    )
    sockets = [ws1, ws2]

    def fake_ws_connect(url: str, **_: Any) -> _FakeWsConnectContext:
        connected_urls.append(url)
        return _FakeWsConnectContext(sockets.pop(0))

    async def instant_sleep(_: float) -> None:
        return None

    async def on_message_received(d: dict[str, Any]) -> None:
        messages_received.append(d)
        await client_ref[0].stop()

    gw = DiscordGatewayClient(
        "bot_secret_token_123",
        on_ready=lambda d: ready_events.append(d),
        on_message=on_message_received,
        ws_connect=fake_ws_connect,
        jitter_fn=lambda: 1.0,
        sleep_fn=instant_sleep,
    )
    client_ref.append(gw)

    await gw.run_forever()

    # Connection 1 used default URL and sent Op 2 Identify + Op 1 Heartbeat
    assert connected_urls[0] == "wss://gateway.discord.gg/?v=10&encoding=json"
    ops_conn1 = [p["op"] for p in ws1.sent_packets]
    assert OP_IDENTIFY in ops_conn1
    assert OP_HEARTBEAT in ops_conn1
    assert len(ready_events) == 1
    assert gw.session_id == "sess_abc_123"

    # Connection 2 used normalized resume_gateway_url and sent Op 6 Resume with seq=1
    assert connected_urls[1] == "wss://us-east1.gateway.discord.gg/?v=10&encoding=json"
    resume_pkt = next(p for p in ws2.sent_packets if p["op"] == OP_RESUME)
    assert resume_pkt["d"]["session_id"] == "sess_abc_123"
    assert resume_pkt["d"]["seq"] == 1
    assert len(messages_received) == 1
    assert messages_received[0]["content"] == "Oi Orin!"


@pytest.mark.anyio
@pytest.mark.parametrize("fatal_code", [4004, 4014])
async def test_gateway_stops_immediately_without_infinite_loop_on_fatal_close_codes(fatal_code: int) -> None:
    connect_attempts = 0

    async def on_send(ws: _FakeWebSocket, pkt: dict[str, Any]) -> None:
        if pkt["op"] == OP_IDENTIFY:
            ws.finish(close_code=fatal_code)

    def fake_ws_connect(url: str, **_: Any) -> _FakeWsConnectContext:
        nonlocal connect_attempts
        connect_attempts += 1
        ws = _FakeWebSocket(
            [{"op": OP_HELLO, "d": {"heartbeat_interval": 60000}}],
            close_code_on_exit=fatal_code,
            on_send=on_send,
        )
        return _FakeWsConnectContext(ws)

    gw = DiscordGatewayClient(
        "invalid_or_unprivileged_token",
        ws_connect=fake_ws_connect,
        sleep_fn=lambda _: asyncio.sleep(0),
    )
    await gw.run_forever()

    assert connect_attempts == 1
    assert gw.status_snapshot.status == "error"
    assert str(fatal_code) in (gw.status_snapshot.last_error or "")


# ----------------------------------------------------------------------
# 2. DiscordBridgeService & SessionWatchCoordinator End-to-End Flow
# ----------------------------------------------------------------------


@pytest.fixture()
def bridge_env(tmp_path: Path):
    db_file = tmp_path / "bridge_test.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    metadata.create_all(engine)

    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret")
    chat_store = PostgresChatStore(engine, activity_store)
    chat_app = ChatApplication(chat_store, ExecutionApplicationAdapter(engine))
    discord_store = PostgresDiscordStore(engine, _make_cipher())
    uploads = UploadStaging(tmp_path / "uploads" / "staging")
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    local_workspaces = PostgresLocalWorkspaceStore(engine)
    project_store = PostgresProjectStore(engine)

    fake_rest_clients: dict[str, _FakeRestClient] = {}

    def rest_factory(token: str) -> _FakeRestClient:
        client = _FakeRestClient(token)
        fake_rest_clients["active"] = client
        return client

    try:
        yield {
            "engine": engine,
            "activity_store": activity_store,
            "chat_store": chat_store,
            "chat_app": chat_app,
            "discord_store": discord_store,
            "uploads": uploads,
            "workspaces_root": workspaces_root,
            "local_workspaces": local_workspaces,
            "project_store": project_store,
            "rest_factory": rest_factory,
            "fake_rest_clients": fake_rest_clients,
        }
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_bridge_start_is_fast_and_silent_when_no_user_is_enabled(bridge_env: dict[str, Any]) -> None:
    created_gateways: list[Any] = []

    def gw_factory(*args: Any, **kwargs: Any) -> _FakeGatewayClient:
        gw = _FakeGatewayClient(*args, **kwargs)
        created_gateways.append(gw)
        return gw

    bridge = DiscordBridgeService(
        discord_store=bridge_env["discord_store"],
        chat_application=bridge_env["chat_app"],
        uploads=bridge_env["uploads"],
        workspace_root=bridge_env["workspaces_root"],
        rest_client_factory=bridge_env["rest_factory"],
        gateway_client_factory=gw_factory,
    )

    await bridge.start()
    assert created_gateways == []
    assert not bridge.watch_coordinator.is_running
    assert bridge.get_user_status("user-1").status == "disconnected"
    await bridge.stop()


@pytest.mark.anyio
async def test_bridge_blocks_unauthorized_users_and_bots_and_processes_authorized_dm_with_attachment(
    bridge_env: dict[str, Any],
) -> None:
    user_id = "user-1"
    allowed_discord_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    bridge_env["discord_store"].save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_a9Xz",
        allowed_discord_user_ids=[allowed_discord_uid],
    )

    chat_store: PostgresChatStore = bridge_env["chat_store"]
    executed_turns: list[tuple[str, str]] = []

    async def fake_turn_executor(conversation_id: str, turn_id: str) -> None:
        executed_turns.append((conversation_id, turn_id))
        claimed = chat_store.claim(turn_id)
        assert claimed is not None
        # Verify live_mode=False was persisted on the queued turn
        assert claimed["live_mode"] is False
        chat_store.delta(claimed, "<speak>Recebi o print e iniciei a análise!</speak>")
        chat_store.finish(claimed)

    bridge = DiscordBridgeService(
        discord_store=bridge_env["discord_store"],
        chat_application=bridge_env["chat_app"],
        uploads=bridge_env["uploads"],
        workspace_root=bridge_env["workspaces_root"],
        rest_client_factory=bridge_env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
        turn_executor=fake_turn_executor,
    )

    await bridge.start()
    try:
        rest: _FakeRestClient = bridge_env["fake_rest_clients"]["active"]

        # 1. Message from a bot is ignored
        res_bot = await bridge.handle_message_create(
            user_id,
            {
                "id": "300000000000000001",
                "channel_id": dm_channel_id,
                "author": {"id": allowed_discord_uid, "bot": True},
                "content": "Sou um bot",
            },
        )
        assert res_bot is None
        assert executed_turns == []

        # 2. Message from a Discord user outside allowlist is ignored (fail-closed)
        res_unauth = await bridge.handle_message_create(
            user_id,
            {
                "id": "300000000000000002",
                "channel_id": dm_channel_id,
                "author": {"id": "999999999999999999"},
                "content": "Tentativa de invasão",
            },
        )
        assert res_unauth is None
        assert executed_turns == []

        # 3. Message in an unallowed guild channel is ignored
        res_guild = await bridge.handle_message_create(
            user_id,
            {
                "id": "300000000000000003",
                "guild_id": "444444444444444444",
                "channel_id": "555555555555555555",
                "author": {"id": allowed_discord_uid},
                "content": "Mensagem em servidor não autorizado",
            },
        )
        assert res_guild is None
        assert executed_turns == []

        # 4. Authorized DM with attachment (and no text -> uses default prompt)
        res_ok = await bridge.handle_message_create(
            user_id,
            {
                "id": "300000000000000004",
                "channel_id": dm_channel_id,
                "author": {"id": allowed_discord_uid},
                "content": "",
                "attachments": [
                    {
                        "filename": "erro.png",
                        "url": "https://cdn.discordapp.com/attachments/111/222/erro.png",
                        "size": 120,
                    }
                ],
            },
        )
        assert res_ok is not None
        assert len(executed_turns) == 1
        friend_cid = res_ok["conversation_id"]

        # Verify default_notify_channel_id was updated
        cfg = bridge_env["discord_store"].get_config(user_id)
        assert cfg is not None
        assert cfg.default_notify_channel_id == dm_channel_id

        # Verify attachment was promoted into the Friend conversation workspace and message sent
        friend_conv = chat_store.get(friend_cid, user_id)
        user_msgs = [m for m in friend_conv["messages"] if m["role"] == "user"]
        assert user_msgs[-1]["content"] == DEFAULT_ATTACHMENT_ONLY_PROMPT
        assert len(user_msgs[-1]["attachments"]) == 1
        assert user_msgs[-1]["attachments"][0]["original_name"] == "erro.png"

        # Verify typing indicator and stripped `<speak>` reply on Discord
        assert dm_channel_id in rest.typing_channels
        assert len(rest.sent_messages) == 1
        assert rest.sent_messages[0]["content"] == "Recebi o print e iniciei a análise!"
        assert "<speak>" not in rest.sent_messages[0]["content"]
    finally:
        await bridge.stop()


@pytest.mark.anyio
async def test_session_watch_coordinator_debounced_progress_buttons_interaction_and_idempotent_wakeup(
    bridge_env: dict[str, Any],
    tmp_path: Path,
) -> None:
    user_id = "user-1"
    allowed_discord_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    discord_store: PostgresDiscordStore = bridge_env["discord_store"]
    chat_store: PostgresChatStore = bridge_env["chat_store"]

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_a9Xz",
        allowed_discord_user_ids=[allowed_discord_uid],
        default_notify_channel_id=dm_channel_id,
    )

    current_time = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)

    def fake_clock() -> datetime:
        return current_time

    wakeup_turns: list[tuple[str, str]] = []

    async def fake_turn_executor(conversation_id: str, turn_id: str) -> None:
        conv = chat_store.get(conversation_id, user_id)
        if conv.get("mode") == "friend":
            wakeup_turns.append((conversation_id, turn_id))
            claimed = chat_store.claim(turn_id)
            assert claimed is not None
            chat_store.delta(
                claimed,
                "A sessão do Orin Code concluiu todas as alterações e passou nos testes!",
            )
            chat_store.finish(claimed)

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=bridge_env["chat_app"],
        uploads=bridge_env["uploads"],
        workspace_root=bridge_env["workspaces_root"],
        local_workspaces=bridge_env["local_workspaces"],
        project_store=bridge_env["project_store"],
        rest_client_factory=bridge_env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
        turn_executor=fake_turn_executor,
        progress_debounce_seconds=5.0,
        clock=fake_clock,
    )

    await bridge.reload_user(user_id)
    rest: _FakeRestClient = bridge_env["fake_rest_clients"]["active"]

    try:
        # 1. Create a watched Code session
        friend_conv = chat_store.ensure_friend_conversation(user_id)
        code_receipt = chat_store.create(
            user_id=user_id,
            message="Refatore o módulo de autenticação",
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            idempotency_key="code_session_1",
            code_mode="code",
            permission_mode="accept_edits",
        )
        code_cid = code_receipt.conversation_id
        code_turn_id = code_receipt.turn_id
        code_turn = chat_store.claim(code_turn_id)
        assert code_turn is not None

        code_ws = bridge_env["workspaces_root"] / code_cid
        code_ws.mkdir(parents=True, exist_ok=True)
        bridge_env["local_workspaces"].set_root(f"workspace:{code_cid}", user_id, str(code_ws))

        watch = discord_store.upsert_watch(
            user_id,
            code_cid,
            code_turn_id,
            friend_conversation_id=str(friend_conv["conversation_id"]),
            origin="friend",
            discord_channel_id=dm_channel_id,
        )

        # Tick 1: Creates the initial Live Progress Card with [Parar sessão] button
        await bridge.watch_coordinator.tick()
        assert len(rest.sent_messages) == 1
        progress_card = rest.sent_messages[0]
        progress_msg_id = progress_card["id"]
        assert "🔄" in progress_card["embeds"][0]["title"]
        assert progress_card["components"][0]["components"][0]["label"] == "Parar sessão"

        # Emit an activity event (TodoWrite + file edit) 2 seconds later (< 5s debounce)
        chat_store._activity(
            code_turn,
            AgentActivityEventType.TOOL_FINISHED,
            "Atualizou checklist e editou auth.py",
            {
                "tool_name": "Edit",
                "file_path": "src/auth.py",
                "todos": [{"content": "Refatorar auth.py", "status": "in_progress", "activeForm": "Refatorando auth.py"}],
            },
        )

        current_time = current_time + timedelta(seconds=2)
        await bridge.watch_coordinator.tick()
        # Debounce (< 5s) prevents intermediate PATCH
        assert len(rest.edited_messages) == 0

        # Advance clock past 5s debounce -> Tick patches the Live Progress Card
        current_time = current_time + timedelta(seconds=4)
        await bridge.watch_coordinator.tick()
        assert len(rest.edited_messages) == 1
        assert "src/auth.py" in json.dumps(rest.edited_messages[0]["embeds"][0], ensure_ascii=False)

        # 2. Session requests ToolPermissionBroker approval -> Coordinator publishes Decision Card
        broker = ToolPermissionBroker(code_ws)
        perm_req = broker.create_permission_request(
            tool_name="run_command",
            arguments={"command": "pytest tests/unit"},
            turn_id=code_turn_id,
            conversation_id=code_cid,
        )
        req_id = str(perm_req["request_id"])

        await bridge.watch_coordinator.tick()
        # A Decision Card message with 3 buttons (allow_once, allow_always, deny) was sent
        assert len(rest.sent_messages) == 2
        decision_msg = rest.sent_messages[1]
        assert "Permissão de Ferramenta" in decision_msg["embeds"][0]["title"]
        buttons = decision_msg["components"][0]["components"]
        assert len(buttons) == 3
        allow_once_custom_id = buttons[0]["custom_id"]

        # 3. Unauthorized user clicking button gets ephemeral 403-style message
        unauth_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_unauth",
                "token": "tok_unauth",
                "channel_id": dm_channel_id,
                "user": {"id": "999999999999999999"},
                "message": {"id": decision_msg["id"], "embeds": decision_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": allow_once_custom_id},
            },
        )
        assert unauth_res == {"status": "unauthorized"}
        assert rest.acked_interactions[-1]["response_type"] == 4
        assert rest.acked_interactions[-1]["data"]["flags"] == 64

        # 4. Authorized user clicks "Permitir 1x" -> resolves ToolPermissionBroker on disk and disables card
        auth_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_ok_1",
                "token": "tok_ok_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_discord_uid},
                "message": {"id": decision_msg["id"], "embeds": decision_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": allow_once_custom_id},
            },
        )
        assert auth_res is not None
        assert auth_res["status"] == "executed"
        resolved_on_disk = broker.get_request(req_id)
        assert resolved_on_disk is not None
        assert resolved_on_disk["status"] == "resolved"
        assert resolved_on_disk["decision"] == "allow_once"
        assert resolved_on_disk["allowed"] is True

        # 5. Double-click on the same button (or sibling button on the same card) is rejected by consume_binding
        dup_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_dup_2",
                "token": "tok_dup_2",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_discord_uid},
                "message": {"id": decision_msg["id"], "embeds": decision_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": allow_once_custom_id},
            },
        )
        assert dup_res == {"status": "already_consumed_or_expired"}
        assert rest.acked_interactions[-1]["response_type"] == 4

        # 6. Complete the watched Code session -> Final PATCH of Live Card + single idempotent Friend wakeup
        chat_store.delta(code_turn, "Refatoração concluída e validada.")
        chat_store.finish(code_turn)

        await bridge.watch_coordinator.tick()
        updated_watch = discord_store.get_watch(watch.watch_id)
        assert updated_watch is not None
        assert updated_watch.status == "completed"
        assert updated_watch.notified_terminal is True
        assert len(wakeup_turns) == 1
        assert any("concluiu todas as alterações" in (m.get("content") or "") for m in rest.sent_messages)

        # Running another tick does NOT re-trigger a second wakeup turn
        await bridge.watch_coordinator.tick()
        assert len(wakeup_turns) == 1

        # 7. Test [Parar sessão] button on a second watched session
        code_receipt_2 = chat_store.create(
            user_id=user_id,
            message="Tarefa longa para cancelar",
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            idempotency_key="code_session_2",
            code_mode="code",
        )
        watch_2 = discord_store.upsert_watch(
            user_id,
            code_receipt_2.conversation_id,
            code_receipt_2.turn_id,
            friend_conversation_id=str(friend_conv["conversation_id"]),
            origin="friend",
            discord_channel_id=dm_channel_id,
        )
        await bridge.watch_coordinator.tick()
        progress_card_2 = rest.sent_messages[-1]
        stop_custom_id = progress_card_2["components"][0]["components"][0]["custom_id"]

        stop_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_stop_1",
                "token": "tok_stop_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_discord_uid},
                "message": {"id": progress_card_2["id"], "embeds": progress_card_2["embeds"]},
                "data": {"component_type": 2, "custom_id": stop_custom_id},
            },
        )
        assert stop_res is not None
        assert stop_res["status"] == "executed"
        assert stop_res["action_kind"] == "stop_session"

        cancelled_conv = chat_store.get(code_receipt_2.conversation_id, user_id)
        assert cancelled_conv["state"] in {"cancelled", "cancelling"}
        watch_2_after = discord_store.get_watch(watch_2.watch_id)
        assert watch_2_after is not None
        assert watch_2_after.status == "cancelled"
        assert watch_2_after.notified_terminal is True
    finally:
        await bridge.stop()


@pytest.mark.anyio
async def test_string_select_ask_user_interaction_and_project_tools_auto_watch(
    bridge_env: dict[str, Any],
) -> None:
    from agentos.agentic.agent_tools import AgentToolset
    from agentos.agentic.workspace import ConversationWorkspace

    user_id = "user-1"
    allowed_discord_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    discord_store: PostgresDiscordStore = bridge_env["discord_store"]
    chat_store: PostgresChatStore = bridge_env["chat_store"]

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_a9Xz",
        allowed_discord_user_ids=[allowed_discord_uid],
        default_notify_channel_id=dm_channel_id,
    )

    friend_conv = chat_store.ensure_friend_conversation(user_id)
    friend_cid = str(friend_conv["conversation_id"])

    # AgentToolset without explicit on_session_watch still auto-persists watches in PostgresDiscordStore
    workspace = ConversationWorkspace(bridge_env["workspaces_root"], friend_cid)
    toolset = AgentToolset(
        workspace,
        code_mode_active=False,
        conversation_id=friend_cid,
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
        chat_store=chat_store,
        project_store=bridge_env["project_store"],
        local_workspaces=bridge_env["local_workspaces"],
        project_user_id=user_id,
    )

    started_outcome = toolset.manage_session(
        action="start",
        prompt="Escolha a arquitetura do módulo",
        mode="code",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
    )
    started = started_outcome.payload
    target_cid = str(started["conversation_id"])
    target_turn_id = str(started["turn_id"])

    open_watches = discord_store.list_open_watches(user_id=user_id)
    assert len(open_watches) == 1
    watch = open_watches[0]
    assert watch.target_conversation_id == target_cid
    assert watch.target_turn_id == target_turn_id
    assert watch.discord_channel_id == dm_channel_id

    # Put watched session in waiting_user with 7 options -> StringSelect (component_type=3)
    claimed = chat_store.claim(target_turn_id)
    assert claimed is not None
    seven_options = [
        {"label": f"Arquitetura {i}", "value": f"arch_{i}", "description": f"Opção {i}"}
        for i in range(1, 8)
    ]
    chat_store._activity(
        claimed,
        AgentActivityEventType.TURN_WAITING_USER,
        "Aguardando escolha de arquitetura",
        {
            "questions": [
                {
                    "header": "Arquitetura",
                    "question": "Qual arquitetura devemos adotar?",
                    "options": seven_options,
                }
            ]
        },
    )
    chat_store.finish(claimed, code="WAITING_USER")

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=bridge_env["chat_app"],
        uploads=bridge_env["uploads"],
        workspace_root=bridge_env["workspaces_root"],
        local_workspaces=bridge_env["local_workspaces"],
        project_store=bridge_env["project_store"],
        rest_client_factory=bridge_env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
    )
    await bridge.reload_user(user_id)
    rest: _FakeRestClient = bridge_env["fake_rest_clients"]["active"]

    try:
        await bridge.watch_coordinator.tick()
        # Message 0: Live Progress Card; Message 1: Decision Card with StringSelect (type=3)
        assert len(rest.sent_messages) == 2
        decision_msg = rest.sent_messages[1]
        select_comp = decision_msg["components"][0]["components"][0]
        assert select_comp["type"] == 3
        assert len(select_comp["options"]) == 7

        # User selects "arch_5" from the StringSelect
        inter_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_select_1",
                "token": "tok_select_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_discord_uid},
                "message": {"id": decision_msg["id"], "embeds": decision_msg["embeds"]},
                "data": {
                    "component_type": 3,
                    "custom_id": select_comp["custom_id"],
                    "values": ["arch_5"],
                },
            },
        )
        assert inter_res is not None
        assert inter_res["status"] == "executed"
        assert inter_res["action_kind"] == "ask_user"
        assert "Arquitetura 5" in inter_res["label"]

        # Verify user message "arch_5" was sent to the watched Code session
        updated_conv = chat_store.get(target_cid, user_id)
        user_messages = [m["content"] for m in updated_conv["messages"] if m["role"] == "user"]
        assert "arch_5" in user_messages
    finally:
        await bridge.stop()


@pytest.mark.anyio
async def test_redteam_regressions_nonblocking_wakeup_outgoing_file_hardening_and_cli_models(
    bridge_env: dict[str, Any],
    tmp_path: Path,
) -> None:
    from sqlalchemy import insert

    from agentos.persistence.postgres.schema import provider_configurations

    user_id = "user-1"
    allowed_discord_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"
    discord_store: PostgresDiscordStore = bridge_env["discord_store"]
    chat_store: PostgresChatStore = bridge_env["chat_store"]

    # 1. Configure CLI provider (openai-codex) and disabled provider (anthropic) in SQLite
    now_ts = datetime.now(UTC)
    with bridge_env["engine"].begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id=user_id,
                provider="anthropic",
                enabled=False,
                secret_ref="",
                created_at=now_ts,
                updated_at=now_ts,
            )
        )
        conn.execute(
            insert(provider_configurations).values(
                user_id=user_id,
                provider="openai-codex",
                enabled=True,
                model="gpt-5.3-codex",
                secret_ref="",
                created_at=now_ts,
                updated_at=now_ts,
            )
        )

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_a9Xz",
        allowed_discord_user_ids=[allowed_discord_uid],
        default_notify_channel_id=dm_channel_id,
    )

    slow_wakeup_started = asyncio.Event()
    release_slow_wakeup = asyncio.Event()
    tick_lock_held_during_wakeup: list[bool] = []

    async def slow_turn_executor(conversation_id: str, turn_id: str) -> None:
        conv = chat_store.get(conversation_id, user_id)
        if conv.get("mode") == "friend":
            tick_lock_held_during_wakeup.append(bridge.watch_coordinator._tick_lock.locked())
            slow_wakeup_started.set()
            await release_slow_wakeup.wait()
            claimed = chat_store.claim(turn_id)
            if claimed is not None:
                chat_store.delta(claimed, "Resumo final entregue sem travar o tick!")
                chat_store.finish(claimed)

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=bridge_env["chat_app"],
        uploads=bridge_env["uploads"],
        workspace_root=bridge_env["workspaces_root"],
        local_workspaces=bridge_env["local_workspaces"],
        project_store=bridge_env["project_store"],
        rest_client_factory=bridge_env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
        turn_executor=slow_turn_executor,
    )

    # Verify CLI model resolution skips disabled anthropic and picks enabled openai-codex
    resolved_prov, resolved_model = bridge._resolve_configured_friend_model(user_id)
    assert resolved_prov == "openai-codex"
    assert resolved_model == "gpt-5.3-codex"

    await bridge.reload_user(user_id)
    try:
        # 2. Outgoing file hardening: reject dotfiles (.orin, .env, .git) and symlinks
        ws_cid = "conv-sec-test"
        ws_dir = bridge_env["workspaces_root"] / ws_cid
        (ws_dir / ".orin").mkdir(parents=True, exist_ok=True)
        (ws_dir / ".orin" / "secret.json").write_text('{"token":"secret"}', encoding="utf-8")
        (ws_dir / ".env").write_text("SECRET_KEY=123", encoding="utf-8")
        (ws_dir / "reports").mkdir(parents=True, exist_ok=True)
        (ws_dir / "reports" / "ok.txt").write_text("relatorio seguro", encoding="utf-8")

        collected = bridge._collect_outgoing_files(
            user_id=user_id,
            conversation_id=ws_cid,
            assistant_message={},
            text=(
                "Arquivos: workspace://.orin/secret.json "
                "workspace://.env "
                "workspace://reports/ok.txt"
            ),
        )
        assert [f.filename for f in collected] == ["ok.txt"]

        # 3. Non-blocking tick() during slow _trigger_friend_wakeup
        friend_conv = chat_store.ensure_friend_conversation(user_id)
        code_receipt = chat_store.create(
            user_id=user_id,
            message="Tarefa rápida",
            provider="openai-codex",
            model_id="gpt-5.3-codex",
            idempotency_key="code_nonblock_1",
            code_mode="code",
        )
        code_turn = chat_store.claim(code_receipt.turn_id)
        assert code_turn is not None
        watch = discord_store.upsert_watch(
            user_id,
            code_receipt.conversation_id,
            code_receipt.turn_id,
            friend_conversation_id=str(friend_conv["conversation_id"]),
            origin="friend",
            discord_channel_id=dm_channel_id,
        )
        await bridge.watch_coordinator.tick()
        runner = bridge._runners[user_id]
        assert watch.watch_id in runner.stop_bindings_by_watch

        # Finish watched session; tick() must return quickly even though wakeup turn waits on release_slow_wakeup
        chat_store.delta(code_turn, "Concluído.")
        chat_store.finish(code_turn)

        await asyncio.wait_for(bridge.watch_coordinator.tick(), timeout=2.5)
        # notified_terminal is already persisted and stop binding cache cleaned up while wakeup is still running
        updated_watch = discord_store.get_watch(watch.watch_id)
        assert updated_watch is not None
        assert updated_watch.notified_terminal is True
        assert updated_watch.status == "completed"
        assert watch.watch_id not in runner.stop_bindings_by_watch
        assert tick_lock_held_during_wakeup == [False]

        release_slow_wakeup.set()
        await bridge.wait_background_tasks(user_id=user_id, timeout=2.0)
    finally:
        release_slow_wakeup.set()
        await bridge.stop()

