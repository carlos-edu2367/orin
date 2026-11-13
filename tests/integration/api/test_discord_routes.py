"""Integration tests for `/v1/integrations/discord` REST routes."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from agentos.discord.gateway_client import DiscordRuntimeStatus
from agentos.discord.rest_client import DiscordApiError
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.schema import discord_bot_configs
from tests.integration.server_world import build_server_world


class _FakeGatewayClient:
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
        self.on_status_change = on_status_change
        self.started = False
        self.stopped = False
        self._status = DiscordRuntimeStatus(
            status="connected",
            last_connected_at=datetime.now(UTC),
            bot_user_id="999888777666555444",
            bot_username="OrinFriendBot",
            latency_ms=42.5,
            bot_application_id="111222333444555666",
        )

    @property
    def status_snapshot(self) -> DiscordRuntimeStatus:
        return self._status

    async def start(self) -> None:
        self.started = True
        if callable(self.on_status_change):
            res = self.on_status_change(self._status)
            if hasattr(res, "__await__"):
                await res

    async def stop(self) -> None:
        self.stopped = True
        self._status = DiscordRuntimeStatus(
            status="disconnected",
            bot_user_id="999888777666555444",
            bot_username="OrinFriendBot",
            bot_application_id="111222333444555666",
        )


class _FakeDiscordRestClient:
    def __init__(
        self,
        bot_token: str,
        *,
        fail_auth: bool = False,
        fail_dm_403: bool = False,
    ) -> None:
        self.bot_token = bot_token
        self.fail_auth = fail_auth
        self.fail_dm_403 = fail_dm_403
        self.opened_dms: list[str] = []
        self.sent_messages: list[tuple[str, str | None]] = []
        self.closed = False

    async def get_current_bot(self) -> dict[str, Any]:
        if self.fail_auth:
            raise DiscordApiError(
                "401: Unauthorized",
                status_code=401,
                error_code=0,
            )
        return {
            "id": "999888777666555444",
            "username": "OrinFriendBot",
            "discriminator": "0",
            "application_id": "111222333444555666",
        }

    async def open_dm(self, discord_user_id: str) -> dict[str, Any]:
        self.opened_dms.append(discord_user_id)
        if self.fail_dm_403:
            raise DiscordApiError(
                "Cannot send messages to this user",
                status_code=403,
                error_code=50007,
            )
        return {"id": "777666555444333222", "type": 1}

    async def send_message(self, channel_id: str, *, content: str | None = None, **_: Any) -> dict[str, Any]:
        if self.fail_dm_403:
            raise DiscordApiError(
                "Cannot send messages to this user",
                status_code=403,
                error_code=50007,
            )
        self.sent_messages.append((channel_id, content))
        return {"id": "888777666555444333", "channel_id": channel_id, "content": content}

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    w = build_server_world(tmp_path, monkeypatch)
    # Replace gateway_client_factory so tests never open real WebSockets to Discord
    w.services.discord_bridge._gateway_client_factory = _FakeGatewayClient
    w.services.discord_bridge._rest_client_factory = lambda token: _FakeDiscordRestClient(token)
    yield w
    reset_cached_paths()


def test_discord_routes_get_put_encryption_masking_and_preserve_token(world):
    user = world.admin()
    raw_token = "MTIzNDU2Nzg5MDEyMzQ1Njc4.superSecretBotTokenValue.a9Xz"

    # 1. Initial GET returns clean unconfigured state
    initial = user.api.get("/v1/integrations/discord")
    assert initial.status_code == 200, initial.text
    initial_data = initial.json()
    assert initial_data["enabled"] is False
    assert initial_data["has_bot_token"] is False
    assert initial_data["masked_token_hint"] is None
    assert initial_data["allowed_discord_user_ids"] == []
    assert initial_data["allowed_channel_ids"] == []
    assert initial_data["runtime"]["status"] == "disconnected"

    # 2. Fail-closed validation: enabling without token or without allowed_discord_user_ids fails with 400
    no_token_resp = user.api.put(
        "/v1/integrations/discord",
        json={
            "enabled": True,
            "allowed_discord_user_ids": ["123456789012345678"],
        },
        headers=user.headers(),
    )
    assert no_token_resp.status_code == 400
    assert "Bot Token" in no_token_resp.text

    no_allowlist_resp = user.api.put(
        "/v1/integrations/discord",
        json={
            "enabled": True,
            "bot_token": raw_token,
            "allowed_discord_user_ids": [],
        },
        headers=user.headers(),
    )
    assert no_allowlist_resp.status_code == 400
    assert "allowlist" in no_allowlist_resp.text.lower() or "User ID" in no_allowlist_resp.text

    # 3. Snowflake validation: non-numeric or wrong length IDs fail with 400
    bad_snowflake_resp = user.api.put(
        "/v1/integrations/discord",
        json={
            "enabled": False,
            "bot_token": raw_token,
            "allowed_discord_user_ids": ["not-a-snowflake-123"],
        },
        headers=user.headers(),
    )
    assert bad_snowflake_resp.status_code == 400
    assert "17 a 20" in bad_snowflake_resp.text

    # 4. Valid PUT saves encrypted token, never leaks plaintext, and connects via bridge
    put_resp = user.api.put(
        "/v1/integrations/discord",
        json={
            "enabled": True,
            "bot_token": raw_token,
            "allowed_discord_user_ids": ["123456789012345678"],
            "allowed_channel_ids": ["234567890123456789"],
        },
        headers=user.headers(),
    )
    assert put_resp.status_code == 200, put_resp.text
    assert raw_token not in put_resp.text
    put_data = put_resp.json()
    assert put_data["enabled"] is True
    assert put_data["has_bot_token"] is True
    assert put_data["masked_token_hint"] == "...a9Xz"
    assert put_data["allowed_discord_user_ids"] == ["123456789012345678"]
    assert put_data["allowed_channel_ids"] == ["234567890123456789"]
    assert put_data["runtime"]["status"] == "connected"
    assert put_data["runtime"]["bot_username"] == "OrinFriendBot"

    # Verify DB row stores `enc:v1:...` ciphertext and never plaintext
    with world.engine.connect() as conn:
        row = (
            conn.execute(
                select(discord_bot_configs).where(discord_bot_configs.c.user_id == user.user_id)
            )
            .mappings()
            .one()
        )
    assert str(row["bot_token_ciphertext"]).startswith("enc:v1:")
    assert raw_token not in str(row["bot_token_ciphertext"])
    assert row["bot_token_hint"] == "...a9Xz"

    # 5. Subsequent PUT with omitted/empty bot_token preserves existing token
    update_resp = user.api.put(
        "/v1/integrations/discord",
        json={
            "enabled": True,
            "bot_token": "",
            "allowed_discord_user_ids": ["123456789012345678", "123456789012345679"],
            "allowed_channel_ids": [],
        },
        headers=user.headers(),
    )
    assert update_resp.status_code == 200, update_resp.text
    update_data = update_resp.json()
    assert update_data["has_bot_token"] is True
    assert update_data["masked_token_hint"] == "...a9Xz"
    assert update_data["allowed_discord_user_ids"] == ["123456789012345678", "123456789012345679"]
    assert update_data["allowed_channel_ids"] == []
    assert world.services.discord_store.get_decrypted_bot_token(user.user_id) == raw_token


def test_discord_test_endpoint_and_delete(world):
    user = world.admin()
    raw_token = "MTIzNDU2Nzg5MDEyMzQ1Njc4.validTokenForTest.z8Yw"

    # 1. Calling POST /test when no token is saved or passed returns 400
    missing_resp = user.api.post("/v1/integrations/discord/test", json={}, headers=user.headers())
    assert missing_resp.status_code == 400
    assert "Nenhum Bot Token configurado" in missing_resp.text

    # 2. Calling POST /test with an invalid token rejected by Discord returns 400
    created_clients: list[_FakeDiscordRestClient] = []

    def failing_factory(token: str) -> _FakeDiscordRestClient:
        c = _FakeDiscordRestClient(token, fail_auth=True)
        created_clients.append(c)
        return c

    world.services.discord_bridge._rest_client_factory = failing_factory
    bad_token_resp = user.api.post(
        "/v1/integrations/discord/test",
        json={"bot_token": "MTIzNDU2Nzg5MDEyMzQ1Njc4.invalidToken.0000"},
        headers=user.headers(),
    )
    assert bad_token_resp.status_code == 400
    assert "401" in bad_token_resp.text or "inválido" in bad_token_resp.text.lower()
    assert created_clients[-1].closed is True

    # 3. Save config and run POST /test with DM delivery success
    def ok_factory(token: str) -> _FakeDiscordRestClient:
        c = _FakeDiscordRestClient(token)
        created_clients.append(c)
        return c

    world.services.discord_bridge._rest_client_factory = ok_factory

    save_resp = user.api.put(
        "/v1/integrations/discord",
        json={
            "enabled": False,
            "bot_token": raw_token,
            "allowed_discord_user_ids": ["123456789012345678"],
        },
        headers=user.headers(),
    )
    assert save_resp.status_code == 200

    test_resp = user.api.post("/v1/integrations/discord/test", json={}, headers=user.headers())
    assert test_resp.status_code == 200, test_resp.text
    test_data = test_resp.json()
    assert test_data["ok"] is True
    assert test_data["dm_sent"] is True
    assert test_data["dm_channel_id"] == "777666555444333222"
    assert test_data["bot_user_id"] == "999888777666555444"
    assert test_data["bot_username"] == "OrinFriendBot"
    assert test_data["default_notify_channel_id"] == "777666555444333222"
    assert created_clients[-1].opened_dms == ["123456789012345678"]
    assert created_clients[-1].sent_messages == [
        ("777666555444333222", "Orin conectado a este PC e pronto para comandos via Orin Friend.")
    ]

    # 4. When Discord returns 403 on DM open/send, endpoint returns partial success with explanatory warning
    world.services.discord_bridge._rest_client_factory = lambda token: _FakeDiscordRestClient(
        token, fail_dm_403=True
    )
    dm_blocked_resp = user.api.post("/v1/integrations/discord/test", json={}, headers=user.headers())
    assert dm_blocked_resp.status_code == 200, dm_blocked_resp.text
    dm_blocked_data = dm_blocked_resp.json()
    assert dm_blocked_data["ok"] is True
    assert dm_blocked_data["dm_sent"] is False
    assert dm_blocked_data["warning"] is not None
    assert "servidor" in dm_blocked_data["warning"].lower()

    # 5. DELETE stops runner and wipes stored token/config
    del_resp = user.api.delete("/v1/integrations/discord", headers=user.headers())
    assert del_resp.status_code == 200, del_resp.text
    del_data = del_resp.json()
    assert del_data["deleted"] is True
    assert del_data["enabled"] is False
    assert del_data["has_bot_token"] is False
    assert del_data["masked_token_hint"] is None
    assert del_data["allowed_discord_user_ids"] == []
    assert world.services.discord_store.get_config(user.user_id) is None
