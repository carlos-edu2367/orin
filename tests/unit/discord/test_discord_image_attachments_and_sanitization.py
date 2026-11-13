"""Unit tests for Discord image/screenshot multipart delivery and residual Tool Use/Result sanitization."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.discord import (
    DiscordBridgeService,
    DiscordFileAttachment,
    DiscordRestClient,
    DiscordRuntimeStatus,
    PostgresDiscordStore,
    strip_residual_speak_tags,
)
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.projects import PostgresProjectStore
from agentos.uploads.staging import UploadStaging


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
WEBP_BYTES = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x000\x01\x00\x9d\x01*\x01\x00\x01\x00\x00\x00\x00\x00"


def _make_cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher("test-master-secret-key-for-discord-images-0060")


class _FakeRestClient:
    def __init__(self, bot_token: str = "fake_token") -> None:
        self.bot_token = bot_token
        self.sent_messages: list[dict[str, Any]] = []
        self._seq = 300000000000000000

    async def aclose(self) -> None:
        return None

    async def trigger_typing(self, channel_id: str) -> None:
        return None

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
        self._seq += 1
        rec = {
            "id": str(self._seq),
            "channel_id": channel_id,
            "content": content,
            "embeds": list(embeds) if embeds else [],
            "components": list(components) if components else [],
            "files": list(files) if files else [],
            "reply_to_message_id": reply_to_message_id,
        }
        self.sent_messages.append(rec)
        return rec


class _FakeGatewayClient:
    def __init__(self, bot_token: str, *, on_status_change: Any = None, **_: Any) -> None:
        self.bot_token = bot_token
        self.on_status_change = on_status_change
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
        async def _noop() -> None:
            if callable(self.on_status_change):
                await self.on_status_change(self._status)

        return asyncio.create_task(_noop())

    async def stop(self) -> None:
        self._status = DiscordRuntimeStatus(status="disconnected")


@pytest.fixture
def env(tmp_path: Path) -> Any:
    db_file = tmp_path / "discord_images_test.db"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    metadata.create_all(engine)

    cipher = _make_cipher()
    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret-images")
    chat_store = PostgresChatStore(engine, activity_store)
    chat_app = ChatApplication(chat_store, ExecutionApplicationAdapter(engine))
    discord_store = PostgresDiscordStore(engine, cipher)
    uploads = UploadStaging(tmp_path / "uploads" / "staging")
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    local_workspaces = PostgresLocalWorkspaceStore(engine)
    project_store = PostgresProjectStore(engine)

    user_id = "usr_carlos"
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id=user_id,
                provider="openai-codex",
                enabled=True,
                base_url=None,
                secret_ref="secret:openai-codex",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                risk_acknowledged_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5.fake.token",
        allowed_discord_user_ids=["777111222333444555"],
    )

    fake_rest = _FakeRestClient()
    try:
        yield {
            "user_id": user_id,
            "engine": engine,
            "chat_store": chat_store,
            "chat_app": chat_app,
            "discord_store": discord_store,
            "uploads": uploads,
            "workspaces_root": workspaces_root,
            "local_workspaces": local_workspaces,
            "project_store": project_store,
            "fake_rest": fake_rest,
            "tmp_path": tmp_path,
        }
    finally:
        engine.dispose()


def test_strip_residual_speak_tags_removes_leaked_tool_use_and_result_blocks() -> None:
    leaked = (
        "<speak>Vou checar agora.</speak>\n\n"
        "&#91;Tool Use id=call_abc name=manage_session]\n"
        '{"name": "manage_session", "arguments": {"action": "inspect"}}\n'
        "&#91;/Tool Use]&#91;Tool Result id=call_abc name=manage_session status=succeeded]\n"
        '{"ok": true, "state": "completed"}\n'
        "&#91;/Tool Result]AssistantA tela está funcionando normalmente."
    )
    cleaned = strip_residual_speak_tags(leaked)
    assert "Tool Use" not in cleaned
    assert "Tool Result" not in cleaned
    assert "&#91;" not in cleaned
    assert "<speak>" not in cleaned
    assert "AssistantA" not in cleaned
    assert "Vou checar agora." in cleaned
    assert "A tela está funcionando normalmente." in cleaned


@pytest.mark.anyio
async def test_discord_bridge_attaches_png_jpg_webp_from_friend_and_watched_code_session_and_strips_raw_uris(
    env: dict[str, Any],
) -> None:
    user_id = env["user_id"]
    chat_store: PostgresChatStore = env["chat_store"]
    fake_rest: _FakeRestClient = env["fake_rest"]

    # Create a local project folder and attach a Code session to it with Playwright & generated screenshots
    project_dir = env["tmp_path"] / "projeto_educly"
    (project_dir / ".orin" / "playwright").mkdir(parents=True, exist_ok=True)
    (project_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    playwright_png = project_dir / ".orin" / "playwright" / "tela login.png"
    playwright_png.write_bytes(PNG_BYTES)
    local_webp = project_dir / "screenshots" / "painel.webp"
    local_webp.write_bytes(WEBP_BYTES)
    # Sensitive dotfile in same project must stay blocked
    (project_dir / ".orin" / "secret.json").write_text('{"secret": 1}', encoding="utf-8")

    code_receipt = chat_store.create(
        user_id=user_id,
        message="Tire print da tela de login",
        provider="openai-codex",
        model_id="gpt-5.3-codex",
        idempotency_key="code_shot_1",
        code_mode="code",
    )
    env["local_workspaces"].set_root(f"workspace:{code_receipt.conversation_id}", user_id, str(project_dir))
    env["discord_store"].upsert_watch(
        user_id,
        code_receipt.conversation_id,
        code_receipt.turn_id,
        origin="friend",
        discord_channel_id="888000111222333444",
    )

    def turn_executor(conversation_id: str, turn_id: str) -> None:
        claimed = chat_store.claim(turn_id)
        assert claimed is not None
        friend_ws = ConversationWorkspace(env["workspaces_root"], conversation_id)
        jpg_path = friend_ws.resolve("generated/banner promo.jpg")
        jpg_path.parent.mkdir(parents=True, exist_ok=True)
        jpg_path.write_bytes(JPEG_BYTES)

        # Simulate leaked tool block + markdown workspace:// links (URL-encoded space) + Playwright screenshot + local absolute path
        chat_store.delta(
            claimed,
            "&#91;Tool Use id=call_1 name=manage_session]\n"
            '{"name": "manage_session", "arguments": {"action": "inspect"}}\n'
            "&#91;/Tool Use]&#91;Tool Result id=call_1]\n"
            '{"ok": true}\n'
            "&#91;/Tool Result]AssistantAqui estão as capturas geradas:\n"
            "![Tela Login](workspace://.orin/playwright/tela%20login.png)\n"
            "![Banner](workspace://generated/banner%20promo.jpg)\n"
            f"![Painel]({local_webp.as_posix()})\n"
            "workspace://.orin/secret.json",
        )
        chat_store.finish(claimed)

    bridge = DiscordBridgeService(
        discord_store=env["discord_store"],
        chat_application=env["chat_app"],
        uploads=env["uploads"],
        workspace_root=env["workspaces_root"],
        local_workspaces=env["local_workspaces"],
        project_store=env["project_store"],
        rest_client_factory=lambda _tok: fake_rest,
        gateway_client_factory=_FakeGatewayClient,
        turn_executor=turn_executor,
    )

    await bridge.reload_user(user_id)
    try:
        await bridge.handle_message_create(
            user_id,
            {
                "id": "900000000000000001",
                "channel_id": "888000111222333444",
                "author": {"id": "777111222333444555", "username": "carlos"},
                "content": "Me manda os prints da tela",
                "attachments": [],
            },
        )

        assert len(fake_rest.sent_messages) == 1
        sent = fake_rest.sent_messages[0]
        content = sent["content"] or ""
        assert "Tool Use" not in content
        assert "Tool Result" not in content
        assert "&#91;" not in content
        assert "workspace://.orin/playwright" not in content
        assert "workspace://generated" not in content
        assert "Aqui estão as capturas geradas:" in content

        files: list[DiscordFileAttachment] = sent["files"]
        by_name = {f.filename: f for f in files}
        assert set(by_name.keys()) == {"tela login.png", "banner promo.jpg", "painel.webp"}
        assert by_name["tela login.png"].content == PNG_BYTES
        assert by_name["tela login.png"].content_type == "image/png"
        assert by_name["banner promo.jpg"].content == JPEG_BYTES
        assert by_name["banner promo.jpg"].content_type == "image/jpeg"
        assert by_name["painel.webp"].content == WEBP_BYTES
        assert by_name["painel.webp"].content_type == "image/webp"
    finally:
        await bridge.stop()


@pytest.mark.anyio
async def test_discord_rest_client_multipart_upload_sends_image_attachments_payload() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"id": "555666777888999000", "channel_id": "123456"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        rest = DiscordRestClient("fake-bot-token", client=http_client)
        result = await rest.send_message(
            "123456",
            content="Captura pronta",
            files=[
                DiscordFileAttachment(filename="shot.png", content=PNG_BYTES, content_type="image/png"),
                DiscordFileAttachment(filename="card.webp", content=WEBP_BYTES, content_type="image/webp"),
            ],
        )

    assert result["id"] == "555666777888999000"
    assert len(captured_requests) == 1
    req = captured_requests[0]
    ctype = req.headers.get("content-type", "")
    assert ctype.startswith("multipart/form-data;")
    raw_body = req.read()
    assert b'name="payload_json"' in raw_body
    assert b'"filename": "shot.png"' in raw_body
    assert b'"filename": "card.webp"' in raw_body
    assert b"Content-Type: image/png" in raw_body
    assert b"Content-Type: image/webp" in raw_body
    assert PNG_BYTES in raw_body
    assert WEBP_BYTES in raw_body
