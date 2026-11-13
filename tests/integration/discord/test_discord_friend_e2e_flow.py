"""End-to-end integration tests for Discord Bot + Orin Friend 2.0 session orchestration.

Covers the complete flow over real SQLite + PostgresChatStore + PostgresDiscordStore +
PostgresProjectStore + PostgresLocalWorkspaceStore + PostgresProviderCatalogRepository +
AgentToolset (ProjectTools + ModelCatalogTools) + DiscordBridgeService +
SessionWatchCoordinator with a mocked DiscordRestClient:

- Scenario 1: Remote project control (`Chat` and `Code`) with text + image/log attachments,
  `list_models`, `manage_session(action="start")` with explicit `provider`/`model_id` and
  `permission_mode="full_autonomy"`, `live_mode=False` (no `<speak>`), real project/workspace
  binding, and automatic `friend_session_watches` registration.
- Scenario 2: Live Progress Card (`TodoWrite` checklist, modified files, `[Parar sessão]` button,
  `>= 5s` debounce), `waiting_user` Decision Cards (`AskUserQuestion` with Buttons,
  `AskUserQuestion` with >5 options as `StringSelect`, and `ToolPermissionBroker`),
  unauthorized click rejection (`flags=64`), atomic single-use `consume_binding` on double click,
  terminal Live Card update (removing `[Parar sessão]`), and single idempotent Orin Friend wakeup
  (`notified_terminal=True`, `live_mode=False`) delivering final summary + `workspace://` file attachment.
- Scenario 3: On-demand `manage_session(action="inspect")` reading live state, activities,
  `TodoWrite`, and messages, followed by clicking `[Parar sessão]` on Discord to cancel the
  active turn and update the Live Progress Card to `cancelled`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.permissions import ToolPermissionBroker
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.discord import (
    COLOR_CANCELLED,
    COLOR_COMPLETED,
    COLOR_RUNNING,
    COLOR_WAITING_USER,
    DiscordBridgeService,
    DiscordRuntimeStatus,
    PostgresDiscordStore,
)
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.projects.store import PostgresProjectStore
from agentos.provider_catalog.models import PricingSummary, ProviderCatalogContext, ProviderModelRecord
from agentos.uploads.staging import UploadStaging


class _MockDiscordGatewayClient:
    """Deterministic stub for DiscordGatewayClient in E2E bridge integration tests."""

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
        self._status = DiscordRuntimeStatus(
            status="connected",
            last_connected_at=datetime.now(UTC),
            bot_user_id="999888777666555444",
            bot_username="OrinFriendBot",
            latency_ms=28.0,
            bot_application_id="111222333444555666",
        )

    @property
    def status_snapshot(self) -> DiscordRuntimeStatus:
        return self._status

    async def start(self) -> None:
        if callable(self.on_status_change):
            res = self.on_status_change(self._status)
            if hasattr(res, "__await__"):
                await res

    async def stop(self) -> None:
        self._status = DiscordRuntimeStatus(status="disconnected")


class _MockDiscordRestClient:
    """In-memory spy for DiscordRestClient capturing messages, embeds, components, files, and ACKs."""

    def __init__(self, bot_token: str = "fake_bot_token") -> None:
        self.bot_token = bot_token
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.acked_interactions: list[dict[str, Any]] = []
        self.edited_interaction_originals: list[dict[str, Any]] = []
        self.typing_channels: list[str] = []
        self.attachments_by_url: dict[str, bytes] = {
            "https://cdn.discordapp.com/attachments/111222333/444555666/stacktrace.png": (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf"
                b"\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            "https://media.discordapp.net/attachments/111222333/444555667/error.txt": (
                b"Traceback (most recent call last):\n  File 'auth.py', line 42, in login\nValueError: invalid token\n"
            ),
        }
        self._msg_seq = 500000000000000000
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
        data = self.attachments_by_url.get(url, b"default-attachment-bytes")
        if len(data) > max_bytes:
            raise DiscordAttachmentError("Attachment exceeds max_bytes")
        return data


@pytest.fixture()
def e2e_env(tmp_path: Path) -> dict[str, Any]:
    """Build a realistic SQLite-backed environment with all Orin Friend + Discord stores and tools."""
    db_path = tmp_path / "discord_friend_e2e.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    metadata.create_all(engine)

    cipher = ProviderSecretCipher(b"7" * 32)
    user_id = "user-carlos"
    allowed_discord_uid = "123456789012345678"
    unauthorized_discord_uid = "999999999999999999"
    dm_channel_id = "222333444555666777"

    now = datetime(2026, 11, 13, 14, 0, 0, tzinfo=UTC)
    provider_catalog = PostgresProviderCatalogRepository(engine, cipher=cipher)
    ctx = ProviderCatalogContext(user_id=user_id, purpose="e2e_test")

    with engine.begin() as conn:
        for prov in ("anthropic", "google-antigravity", "openai-codex"):
            conn.execute(
                insert(provider_configurations).values(
                    user_id=user_id,
                    provider=prov,
                    enabled=True,
                    base_url=None,
                    secret_ref=f"secret:{prov}",
                    key_cooldown_seconds=60,
                    catalog_refreshed_at=now,
                    risk_acknowledged_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

    provider_catalog.replace(
        ctx,
        "anthropic",
        [
            ProviderModelRecord(
                provider="anthropic",
                model_id="claude-sonnet-4-5",
                display_name="Claude Sonnet 4.5",
                context_window=200_000,
                capabilities=("tools", "thinking", "vision"),
                input_modalities=("text", "image"),
                output_modalities=("text",),
                pricing=PricingSummary("3.0", "15.0"),
                refreshed_at=now,
            ),
        ],
        now,
    )
    provider_catalog.replace(
        ctx,
        "google-antigravity",
        [
            ProviderModelRecord(
                provider="google-antigravity",
                model_id="gemini-4-argon-high",
                display_name="Gemini 4 Argon (High)",
                context_window=1_000_000,
                capabilities=("tools", "vision", "thinking"),
                input_modalities=("text", "image"),
                output_modalities=("text",),
                pricing=None,
                refreshed_at=now,
            ),
        ],
        now,
    )
    provider_catalog.replace(
        ctx,
        "openai-codex",
        [
            ProviderModelRecord(
                provider="openai-codex",
                model_id="gpt-5.3-codex",
                display_name="GPT-5.3 Codex",
                context_window=400_000,
                capabilities=("tools", "thinking"),
                input_modalities=("text",),
                output_modalities=("text",),
                pricing=None,
                refreshed_at=now,
            ),
        ],
        now,
    )
    provider_catalog.set_favorite(ctx, "anthropic", "claude-sonnet-4-5", True)
    provider_catalog.set_favorite(ctx, "google-antigravity", "gemini-4-argon-high", True)
    provider_catalog.set_favorite(ctx, "openai-codex", "gpt-5.3-codex", True)

    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret-e2e")
    chat_store = PostgresChatStore(engine, activity_store)
    chat_app = ChatApplication(chat_store, ExecutionApplicationAdapter(engine))
    uploads = UploadStaging(tmp_path / "staging")
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    local_workspaces = PostgresLocalWorkspaceStore(engine)
    project_store = PostgresProjectStore(engine)
    discord_store = PostgresDiscordStore(engine, cipher=cipher)

    # Create a real project ("Educly") with a real local workspace directory on disk
    project_dir = tmp_path / "projects" / "educly"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "README.md").write_text("# Educly Project\n", encoding="utf-8")

    project = project_store.create(
        user_id=user_id,
        name="Educly",
        description="App principal dos alunos",
    )
    local_workspaces.set_root(project.workspace_id, user_id, str(project_dir))

    # Save active Discord configuration for user-carlos
    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.e2eBotToken.x9Yz",
        allowed_discord_user_ids=[allowed_discord_uid],
        default_notify_channel_id=dm_channel_id,
    )

    mock_rest = _MockDiscordRestClient()

    def build_friend_toolset(friend_conversation_id: str) -> AgentToolset:
        friend_ws = ConversationWorkspace(workspaces_root, friend_conversation_id)
        return AgentToolset(
            friend_ws,
            code_mode_active=False,
            conversation_id=friend_conversation_id,
            default_provider="anthropic",
            default_model_id="claude-sonnet-4-5",
            chat_store=chat_store,
            project_store=project_store,
            local_workspaces=local_workspaces,
            provider_catalog=provider_catalog,
            project_user_id=user_id,
        )

    try:
        yield {
            "engine": engine,
            "user_id": user_id,
            "allowed_discord_uid": allowed_discord_uid,
            "unauthorized_discord_uid": unauthorized_discord_uid,
            "dm_channel_id": dm_channel_id,
            "project": project,
            "project_dir": project_dir,
            "workspaces_root": workspaces_root,
            "uploads": uploads,
            "chat_store": chat_store,
            "chat_app": chat_app,
            "local_workspaces": local_workspaces,
            "project_store": project_store,
            "provider_catalog": provider_catalog,
            "discord_store": discord_store,
            "mock_rest": mock_rest,
            "build_friend_toolset": build_friend_toolset,
        }
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_scenario_1_remote_project_control_chat_and_code_with_attachments_and_auto_watches(
    e2e_env: dict[str, Any],
) -> None:
    """Scenario 1:
    - Authorized Discord user sends a message with text + image/log attachments asking Orin Friend
      to start both a `chat` session and a `code` session in a real project (`project_id`),
      selecting explicit `provider` + `model_id` and `permission_mode="full_autonomy"`.
    - Verifies that the Friend turn was created with `live_mode=False` (no `<speak>`),
      `list_models` discovers the user's configured models, `manage_session(action="start")`
      creates both `chat` and `code` conversations bound to the real `project_id` and real
      workspace directory while preserving `provider`, `model_id`, `title`, and `full_autonomy`,
      and automatically registers watches in `friend_session_watches`.
    """
    user_id = e2e_env["user_id"]
    allowed_uid = e2e_env["allowed_discord_uid"]
    dm_channel_id = e2e_env["dm_channel_id"]
    project = e2e_env["project"]
    project_dir: Path = e2e_env["project_dir"]
    chat_store: PostgresChatStore = e2e_env["chat_store"]
    discord_store: PostgresDiscordStore = e2e_env["discord_store"]
    mock_rest: _MockDiscordRestClient = e2e_env["mock_rest"]
    build_friend_toolset = e2e_env["build_friend_toolset"]

    started_sessions: dict[str, dict[str, Any]] = {}
    catalog_result: dict[str, Any] = {}

    async def friend_turn_executor(conversation_id: str, turn_id: str) -> None:
        claimed = chat_store.claim(turn_id)
        assert claimed is not None
        # Friend turn triggered from Discord must have live_mode=False
        assert claimed["live_mode"] is False

        toolset: AgentToolset = build_friend_toolset(conversation_id)

        # 1. Friend inspects available models via list_models
        models_outcome = toolset.list_models(capability="tools")
        assert models_outcome.status == "succeeded"
        catalog_result.update(models_outcome.payload)

        # 2. Friend starts a Chat session in the Educly project with google-antigravity / gemini-4-argon-high
        chat_start = toolset.manage_session(
            action="start",
            mode="chat",
            project_id=project.project_id,
            title="Diagnóstico de Acesso Educly (Chat)",
            prompt="Analise os logs de erro de login anexados e proponha hipóteses.",
            provider="google-antigravity",
            model_id="gemini-4-argon-high",
            permission_mode="full_autonomy",
        )
        assert chat_start.status == "succeeded"
        started_sessions["chat"] = dict(chat_start.payload)

        # 3. Friend starts a Code session in the Educly project with openai-codex / gpt-5.3-codex
        code_start = toolset.manage_session(
            action="start",
            mode="code",
            project_id=project.project_id,
            title="Correção de Login Educly (Code)",
            prompt="Implemente o fix de validação de token em auth.py com testes.",
            provider="openai-codex",
            model_id="gpt-5.3-codex",
            permission_mode="full_autonomy",
        )
        assert code_start.status == "succeeded"
        started_sessions["code"] = dict(code_start.payload)

        chat_store.delta(
            claimed,
            "Iniciei as duas sessões no projeto **Educly**:\n"
            f"- Chat (`{started_sessions['chat']['conversation_id']}`) com `gemini-4-argon-high`\n"
            f"- Code (`{started_sessions['code']['conversation_id']}`) com `gpt-5.3-codex` em autonomia total.",
        )
        chat_store.finish(claimed)

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=e2e_env["chat_app"],
        uploads=e2e_env["uploads"],
        workspace_root=e2e_env["workspaces_root"],
        local_workspaces=e2e_env["local_workspaces"],
        project_store=e2e_env["project_store"],
        provider_catalog=e2e_env["provider_catalog"],
        rest_client_factory=lambda _token: mock_rest,
        gateway_client_factory=_MockDiscordGatewayClient,
        turn_executor=friend_turn_executor,
    )

    await bridge.reload_user(user_id)
    try:
        msg_result = await bridge.handle_message_create(
            user_id,
            {
                "id": "800000000000000001",
                "channel_id": dm_channel_id,
                "author": {"id": allowed_uid, "username": "carlos"},
                "content": (
                    "Inicie uma sessão Chat (Gemini 4 Argon) e uma sessão Code (GPT-5.3 Codex) "
                    "no projeto Educly com autonomia total para analisar este print e log."
                ),
                "attachments": [
                    {
                        "filename": "stacktrace.png",
                        "url": "https://cdn.discordapp.com/attachments/111222333/444555666/stacktrace.png",
                        "size": 95,
                    },
                    {
                        "filename": "error.txt",
                        "url": "https://media.discordapp.net/attachments/111222333/444555667/error.txt",
                        "size": 102,
                    },
                ],
            },
        )
        assert msg_result is not None
        friend_cid = msg_result["conversation_id"]

        # Verify Friend conversation and message attachments
        friend_conv = chat_store.get(friend_cid, user_id)
        assert friend_conv["mode"] == "friend"
        user_messages = [m for m in friend_conv["messages"] if m["role"] == "user"]
        assert len(user_messages) == 1
        assert len(user_messages[0]["attachments"]) == 2
        att_names = {a["original_name"] for a in user_messages[0]["attachments"]}
        assert att_names == {"stacktrace.png", "error.txt"}

        # Verify list_models returned all 3 configured providers
        listed_providers = {m["provider"] for m in catalog_result.get("models", [])}
        assert {"anthropic", "google-antigravity", "openai-codex"} <= listed_providers

        # Verify started Chat session properties in DB
        chat_payload = started_sessions["chat"]
        chat_cid = str(chat_payload["conversation_id"])
        chat_conv = chat_store.get(chat_cid, user_id)
        assert chat_conv["mode"] == "chat"
        assert chat_conv["project_id"] == project.project_id
        assert chat_conv["title"] == "Diagnóstico de Acesso Educly (Chat)"
        assert chat_conv["provider"] == "google-antigravity"
        assert chat_conv["model_id"] == "gemini-4-argon-high"
        assert chat_conv["permission_mode"] == "full_autonomy"
        assert chat_payload["permission_mode"] == "full_autonomy"
        assert chat_payload["public_permission_mode"] == "bypassPermissions"
        assert Path(chat_payload["workspace_path"]).resolve() == project_dir.resolve()
        assert (
            Path(e2e_env["local_workspaces"].root_for(f"workspace:{chat_cid}", user_id)).resolve()
            == project_dir.resolve()
        )

        # Verify started Code session properties in DB
        code_payload = started_sessions["code"]
        code_cid = str(code_payload["conversation_id"])
        code_conv = chat_store.get(code_cid, user_id)
        assert code_conv["mode"] == "code"
        assert code_conv["project_id"] == project.project_id
        assert code_conv["title"] == "Correção de Login Educly (Code)"
        assert code_conv["provider"] == "openai-codex"
        assert code_conv["model_id"] == "gpt-5.3-codex"
        assert code_conv["permission_mode"] == "full_autonomy"
        assert code_payload["permission_mode"] == "full_autonomy"
        assert code_payload["public_permission_mode"] == "bypassPermissions"
        assert Path(code_payload["workspace_path"]).resolve() == project_dir.resolve()
        assert (
            Path(e2e_env["local_workspaces"].root_for(f"workspace:{code_cid}", user_id)).resolve()
            == project_dir.resolve()
        )

        # Verify both sessions were automatically registered in friend_session_watches
        open_watches = discord_store.list_open_watches(user_id=user_id)
        watched_by_cid = {w.target_conversation_id: w for w in open_watches}
        assert chat_cid in watched_by_cid
        assert code_cid in watched_by_cid
        assert watched_by_cid[chat_cid].friend_conversation_id == friend_cid
        assert watched_by_cid[chat_cid].discord_channel_id == dm_channel_id
        assert watched_by_cid[code_cid].friend_conversation_id == friend_cid
        assert watched_by_cid[code_cid].discord_channel_id == dm_channel_id

        # Verify Friend reply delivered on Discord without <speak> tags
        assert len(mock_rest.sent_messages) == 1
        discord_reply = mock_rest.sent_messages[0]
        assert "<speak>" not in (discord_reply["content"] or "")
        assert chat_cid in (discord_reply["content"] or "")
        assert code_cid in (discord_reply["content"] or "")
    finally:
        await bridge.stop()


@pytest.mark.anyio
async def test_scenario_2_live_card_debounce_decision_buttons_select_permission_and_wakeup_with_file(
    e2e_env: dict[str, Any],
) -> None:
    """Scenario 2:
    - `SessionWatchCoordinator` publishes the Live Progress Card on Discord with `TodoWrite`
      checklist, modified files, and `[Parar sessão]` button, respecting `>= 5s` debounce.
    - Target session enters `waiting_user`:
      (a) `AskUserQuestion` with 3 options -> Decision Card with Buttons;
          unauthorized user clicks and gets ephemeral rejection (`flags=64`);
          authorized user clicks -> session unlocks, concurrent second click is atomically
          rejected (`consume_binding`).
      (b) `AskUserQuestion` with 7 options (> 5) -> Decision Card with `StringSelect` (`type=3`);
          authorized user selects option -> session unlocks.
      (c) `ToolPermissionBroker` request -> Decision Card with `Permitir 1x`, `Sempre permitir`, `Negar`;
          authorized user clicks `Permitir 1x` -> broker request resolved on disk.
    - When session finishes (`completed`), the Live Progress Card is patched removing
      `[Parar sessão]`, and Orin Friend receives exactly 1 compact wakeup
      (`notified_terminal=True`, `live_mode=False`) delivering the final summary on Discord
      along with a generated `workspace://` file attachment.
    """
    user_id = e2e_env["user_id"]
    allowed_uid = e2e_env["allowed_discord_uid"]
    unauth_uid = e2e_env["unauthorized_discord_uid"]
    dm_channel_id = e2e_env["dm_channel_id"]
    project = e2e_env["project"]
    project_dir: Path = e2e_env["project_dir"]
    chat_store: PostgresChatStore = e2e_env["chat_store"]
    discord_store: PostgresDiscordStore = e2e_env["discord_store"]
    mock_rest: _MockDiscordRestClient = e2e_env["mock_rest"]
    build_friend_toolset = e2e_env["build_friend_toolset"]

    current_time = datetime(2026, 11, 13, 15, 0, 0, tzinfo=UTC)

    def fake_clock() -> datetime:
        return current_time

    friend_conv = chat_store.ensure_friend_conversation(user_id)
    friend_cid = str(friend_conv["conversation_id"])
    friend_toolset: AgentToolset = build_friend_toolset(friend_cid)

    # Start a watched Code session in the Educly project via manage_session(action="start")
    started = friend_toolset.manage_session(
        action="start",
        mode="code",
        project_id=project.project_id,
        title="Fluxo E2E Educly",
        prompt="Refatore o fluxo de checkout e gere o relatório final.",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        permission_mode="accept_edits",
    ).payload
    code_cid = str(started["conversation_id"])
    turn_1_id = str(started["turn_id"])

    wakeup_turns: list[dict[str, Any]] = []
    dispatched_code_turns: list[tuple[str, str]] = []

    async def turn_executor(conversation_id: str, turn_id: str) -> None:
        conv = chat_store.get(conversation_id, user_id)
        if conv.get("mode") == "friend":
            claimed = chat_store.claim(turn_id)
            assert claimed is not None
            assert claimed["live_mode"] is False
            wakeup_turns.append(dict(claimed))

            # Create a report file in the Friend workspace and reference it via workspace:// URI
            friend_ws = ConversationWorkspace(e2e_env["workspaces_root"], conversation_id)
            report_path = friend_ws.resolve("generated/relatorio_checkout.txt")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                "Relatório E2E Educly: 100% dos testes de checkout aprovados.\n",
                encoding="utf-8",
            )

            chat_store.delta(
                claimed,
                "<speak>A sessão **Fluxo E2E Educly** terminou com sucesso!</speak>\n"
                "Segue o relatório gerado: workspace://generated/relatorio_checkout.txt",
            )
            chat_store.finish(claimed)
        else:
            dispatched_code_turns.append((conversation_id, turn_id))

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=e2e_env["chat_app"],
        uploads=e2e_env["uploads"],
        workspace_root=e2e_env["workspaces_root"],
        local_workspaces=e2e_env["local_workspaces"],
        project_store=e2e_env["project_store"],
        provider_catalog=e2e_env["provider_catalog"],
        rest_client_factory=lambda _token: mock_rest,
        gateway_client_factory=_MockDiscordGatewayClient,
        turn_executor=turn_executor,
        progress_debounce_seconds=5.0,
        clock=fake_clock,
    )

    await bridge.reload_user(user_id)
    try:
        # Claim Turn 1 of the watched Code session
        turn_1 = chat_store.claim(turn_1_id)
        assert turn_1 is not None

        # Tick 1 (t=0s): Publishes initial Live Progress Card with [Parar sessão] button
        await bridge.watch_coordinator.tick()
        assert len(mock_rest.sent_messages) == 1
        live_card_msg = mock_rest.sent_messages[0]
        live_card_msg_id = live_card_msg["id"]
        assert live_card_msg["embeds"][0]["color"] == COLOR_RUNNING
        assert "🔄" in live_card_msg["embeds"][0]["title"]
        assert live_card_msg["components"][0]["components"][0]["label"] == "Parar sessão"

        # Emit TodoWrite + file modification activity at t=2s (< 5s debounce)
        chat_store._activity(
            turn_1,
            AgentActivityEventType.TOOL_FINISHED,
            "Atualizou checklist e editou src/checkout.py",
            {
                "tool_name": "Edit",
                "file_path": "src/checkout.py",
                "todos": [
                    {
                        "content": "Validar gateway de pagamento",
                        "status": "completed",
                        "activeForm": "Validando gateway",
                    },
                    {
                        "content": "Refatorar src/checkout.py",
                        "status": "in_progress",
                        "activeForm": "Refatorando src/checkout.py",
                    },
                ],
            },
        )
        current_time = current_time + timedelta(seconds=2)
        await bridge.watch_coordinator.tick()
        # Debounce (< 5s) suppresses intermediate PATCH
        assert len(mock_rest.edited_messages) == 0

        # Advance clock to t=6s (>= 5s debounce) -> Live Progress Card is patched with TodoWrite & file
        current_time = current_time + timedelta(seconds=4)
        await bridge.watch_coordinator.tick()
        assert len(mock_rest.edited_messages) == 1
        patched_card = mock_rest.edited_messages[-1]
        assert patched_card["id"] == live_card_msg_id
        fields_text = json.dumps(patched_card["embeds"][0]["fields"], ensure_ascii=False)
        assert "Validar gateway de pagamento" in fields_text
        assert "Refatorando src/checkout.py" in fields_text
        assert "src/checkout.py" in fields_text

        # --- Sub-flow 2A: AskUserQuestion with 3 options (Buttons) ---
        chat_store._activity(
            turn_1,
            AgentActivityEventType.TURN_WAITING_USER,
            "Pergunta sobre estratégia de retry",
            {
                "questions": [
                    {
                        "header": "Retry",
                        "question": "Qual estratégia de retry devemos usar no checkout?",
                        "options": [
                            {"label": "Backoff Exponencial", "value": "exponential"},
                            {"label": "Linear 3x", "value": "linear"},
                            {"label": "Sem Retry", "value": "none"},
                        ],
                    }
                ]
            },
        )
        chat_store.finish(turn_1, code="WAITING_USER")

        await bridge.watch_coordinator.tick()
        # Live Card transitions to waiting_user immediately (bypassing debounce) and Decision Card is sent
        assert mock_rest.edited_messages[-1]["embeds"][0]["color"] == COLOR_WAITING_USER
        assert len(mock_rest.sent_messages) == 2
        ask_buttons_msg = mock_rest.sent_messages[-1]
        button_row = ask_buttons_msg["components"][0]["components"]
        assert len(button_row) == 3
        assert button_row[0]["type"] == 2
        assert button_row[0]["label"] == "Backoff Exponencial"
        backoff_custom_id = button_row[0]["custom_id"]

        # Unauthorized user tries to click the button -> ephemeral block (flags=64)
        unauth_click = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_unauth_1",
                "token": "tok_unauth_1",
                "channel_id": dm_channel_id,
                "user": {"id": unauth_uid},
                "message": {"id": ask_buttons_msg["id"], "embeds": ask_buttons_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": backoff_custom_id},
            },
        )
        assert unauth_click == {"status": "unauthorized"}
        assert mock_rest.acked_interactions[-1]["response_type"] == 4
        assert mock_rest.acked_interactions[-1]["data"]["flags"] == 64

        # Authorized user clicks "Backoff Exponencial" -> unlocks session & creates Turn 2
        auth_click = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_auth_1",
                "token": "tok_auth_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": ask_buttons_msg["id"], "embeds": ask_buttons_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": backoff_custom_id},
            },
        )
        assert auth_click is not None
        assert auth_click["status"] == "executed"
        assert auth_click["action_kind"] == "ask_user"

        # Concurrent second click on the same button (or sibling button) is atomically rejected
        second_click = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_dup_1",
                "token": "tok_dup_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": ask_buttons_msg["id"], "embeds": ask_buttons_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": button_row[1]["custom_id"]},
            },
        )
        assert second_click == {"status": "already_consumed_or_expired"}
        assert mock_rest.acked_interactions[-1]["response_type"] == 4
        assert mock_rest.acked_interactions[-1]["data"]["flags"] == 64

        # --- Sub-flow 2B: AskUserQuestion with 7 options (> 5 -> StringSelect) ---
        conv_after_2a = chat_store.get(code_cid, user_id)
        turn_2_id = str(conv_after_2a["turns"][-1]["turn_id"])
        assert turn_2_id != turn_1_id
        turn_2 = chat_store.claim(turn_2_id)
        assert turn_2 is not None

        seven_options = [
            {"label": f"Provedor Gateway {i}", "value": f"gw_{i}", "description": f"Gateway opção {i}"}
            for i in range(1, 8)
        ]
        chat_store._activity(
            turn_2,
            AgentActivityEventType.TURN_WAITING_USER,
            "Escolha do provedor de gateway (7 opções)",
            {
                "questions": [
                    {
                        "header": "Gateway",
                        "question": "Qual provedor de pagamento secundário devemos configurar?",
                        "options": seven_options,
                    }
                ]
            },
        )
        chat_store.finish(turn_2, code="WAITING_USER")

        current_time = current_time + timedelta(seconds=6)
        await bridge.watch_coordinator.tick()
        assert len(mock_rest.sent_messages) == 3
        select_card_msg = mock_rest.sent_messages[-1]
        select_comp = select_card_msg["components"][0]["components"][0]
        assert select_comp["type"] == 3  # Discord StringSelect
        assert len(select_comp["options"]) == 7

        # Authorized user selects "gw_6" from the StringSelect
        select_click = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_select_gw",
                "token": "tok_select_gw",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": select_card_msg["id"], "embeds": select_card_msg["embeds"]},
                "data": {
                    "component_type": 3,
                    "custom_id": select_comp["custom_id"],
                    "values": ["gw_6"],
                },
            },
        )
        assert select_click is not None
        assert select_click["status"] == "executed"
        assert select_click["action_kind"] == "ask_user"
        assert "Provedor Gateway 6" in select_click["label"]

        # --- Sub-flow 2C: ToolPermissionBroker pending request ---
        conv_after_2b = chat_store.get(code_cid, user_id)
        turn_3_id = str(conv_after_2b["turns"][-1]["turn_id"])
        assert turn_3_id != turn_2_id
        turn_3 = chat_store.claim(turn_3_id)
        assert turn_3 is not None

        broker = ToolPermissionBroker(project_dir)
        perm_req = broker.create_permission_request(
            tool_name="run_command",
            arguments={"command": "pytest tests/checkout"},
            turn_id=turn_3_id,
            conversation_id=code_cid,
        )
        perm_req_id = str(perm_req["request_id"])

        current_time = current_time + timedelta(seconds=6)
        await bridge.watch_coordinator.tick()
        assert len(mock_rest.sent_messages) == 4
        perm_card_msg = mock_rest.sent_messages[-1]
        perm_buttons = perm_card_msg["components"][0]["components"]
        assert [b["label"] for b in perm_buttons] == ["Permitir 1x", "Sempre permitir", "Negar"]

        # Authorized user clicks "Permitir 1x"
        perm_click = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_perm_1",
                "token": "tok_perm_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": perm_card_msg["id"], "embeds": perm_card_msg["embeds"]},
                "data": {"component_type": 2, "custom_id": perm_buttons[0]["custom_id"]},
            },
        )
        assert perm_click is not None
        assert perm_click["status"] == "executed"
        assert perm_click["action_kind"] == "tool_permission"

        resolved_perm = broker.get_request(perm_req_id)
        assert resolved_perm is not None
        assert resolved_perm["status"] == "resolved"
        assert resolved_perm["decision"] == "allow_once"
        assert resolved_perm["allowed"] is True

        # --- Sub-flow 2D: Session completes -> Terminal Live Card + Single Friend Wakeup with File ---
        chat_store._activity(
            turn_3,
            AgentActivityEventType.TOOL_FINISHED,
            "pytest tests/checkout: 14 passed in 0.42s",
            {"tool_name": "VerifyProject"},
        )
        chat_store.delta(turn_3, "Checkout refatorado com backoff exponencial e gateway 6.")
        chat_store.finish(turn_3)

        current_time = current_time + timedelta(seconds=6)
        await bridge.watch_coordinator.tick()

        # Verify Live Progress Card was updated to completed and [Parar sessão] was removed
        terminal_patches = [
            edit for edit in mock_rest.edited_messages if edit["id"] == live_card_msg_id
        ]
        assert len(terminal_patches) >= 2
        final_live_card = terminal_patches[-1]
        assert final_live_card["embeds"][0]["color"] == COLOR_COMPLETED
        assert "✅" in final_live_card["embeds"][0]["title"]
        assert final_live_card["components"] == []

        # Verify Orin Friend received exactly 1 compact wakeup turn with live_mode=False
        assert len(wakeup_turns) == 1
        assert wakeup_turns[0]["live_mode"] is False

        # Verify final Discord message from Friend stripped <speak> and attached the workspace:// file
        final_discord_msg = mock_rest.sent_messages[-1]
        assert "<speak>" not in (final_discord_msg["content"] or "")
        assert "Fluxo E2E Educly" in (final_discord_msg["content"] or "")
        assert len(final_discord_msg["files"]) == 1
        assert final_discord_msg["files"][0].filename == "relatorio_checkout.txt"
        assert b"100% dos testes de checkout aprovados" in final_discord_msg["files"][0].content

        # Verify watch is marked notified_terminal=True and a second tick does NOT trigger another wakeup
        assert discord_store.list_open_watches(user_id=user_id) == []
        await bridge.watch_coordinator.tick()
        assert len(wakeup_turns) == 1
    finally:
        await bridge.stop()


@pytest.mark.anyio
async def test_scenario_3_on_demand_inspect_and_cancel_via_stop_session_button(
    e2e_env: dict[str, Any],
) -> None:
    """Scenario 3:
    - `manage_session(action="inspect")` reads live turn state, activities, `TodoWrite` items,
      modified files, and recent messages of an in-progress session.
    - Clicking `[Parar sessão]` on the Live Progress Card in Discord cancels the active turn
      and updates both the watch and the Live Progress Card Embed to `cancelled` without
      triggering a redundant wakeup turn.
    """
    user_id = e2e_env["user_id"]
    allowed_uid = e2e_env["allowed_discord_uid"]
    dm_channel_id = e2e_env["dm_channel_id"]
    project = e2e_env["project"]
    chat_store: PostgresChatStore = e2e_env["chat_store"]
    discord_store: PostgresDiscordStore = e2e_env["discord_store"]
    mock_rest: _MockDiscordRestClient = e2e_env["mock_rest"]
    build_friend_toolset = e2e_env["build_friend_toolset"]

    friend_conv = chat_store.ensure_friend_conversation(user_id)
    friend_cid = str(friend_conv["conversation_id"])
    friend_toolset: AgentToolset = build_friend_toolset(friend_cid)

    # Start a session in the Educly project
    started = friend_toolset.manage_session(
        action="start",
        mode="code",
        project_id=project.project_id,
        title="Migração Longa Educly",
        prompt="Execute a análise completa de índices e migrações.",
        provider="google-antigravity",
        model_id="gemini-4-argon-high",
        permission_mode="full_autonomy",
    ).payload
    target_cid = str(started["conversation_id"])
    target_turn_id = str(started["turn_id"])

    claimed_turn = chat_store.claim(target_turn_id)
    assert claimed_turn is not None
    chat_store._activity(
        claimed_turn,
        AgentActivityEventType.TOOL_FINISHED,
        "Criou script de análise e atualizou TodoWrite",
        {
            "tool_name": "Write",
            "file_path": "scripts/analyze_indexes.py",
            "todos": [
                {
                    "content": "Mapear tabelas críticas",
                    "status": "completed",
                    "activeForm": "Mapeando tabelas críticas",
                },
                {
                    "content": "Testar plano de execução SQL",
                    "status": "in_progress",
                    "activeForm": "Testando plano de execução SQL",
                },
            ],
        },
    )
    chat_store.delta(claimed_turn, "Analisando índices das tabelas de assinaturas...")

    # 1. Inspect the running session on demand via manage_session(action="inspect")
    inspect_outcome = friend_toolset.manage_session(
        action="inspect",
        conversation_id=target_cid,
        message_limit=5,
    )
    assert inspect_outcome.status == "succeeded"
    inspected = inspect_outcome.payload
    assert inspected["conversation_id"] == target_cid
    assert inspected["title"] == "Migração Longa Educly"
    assert inspected["mode"] == "code"
    assert inspected["state"] == "running"
    assert inspected["project_id"] == project.project_id
    assert inspected["provider"] == "google-antigravity"
    assert inspected["model_id"] == "gemini-4-argon-high"
    assert inspected["permission_mode"] == "full_autonomy"
    assert inspected["public_permission_mode"] == "bypassPermissions"
    assert inspected["todos_completed"] == 1
    assert inspected["todos_total"] == 2
    assert "scripts/analyze_indexes.py" in inspected["files_modified"]
    assert any("Criou script de análise" in a["summary"] for a in inspected["recent_activities"])
    assert any("Execute a análise completa" in m["content"] for m in inspected["messages"])

    wakeup_called = False

    async def turn_executor(conversation_id: str, _turn_id: str) -> None:
        nonlocal wakeup_called
        conv = chat_store.get(conversation_id, user_id)
        if conv.get("mode") == "friend":
            wakeup_called = True

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=e2e_env["chat_app"],
        uploads=e2e_env["uploads"],
        workspace_root=e2e_env["workspaces_root"],
        local_workspaces=e2e_env["local_workspaces"],
        project_store=e2e_env["project_store"],
        provider_catalog=e2e_env["provider_catalog"],
        rest_client_factory=lambda _token: mock_rest,
        gateway_client_factory=_MockDiscordGatewayClient,
        turn_executor=turn_executor,
    )

    await bridge.reload_user(user_id)
    try:
        # Publish Live Progress Card via WatchCoordinator tick
        await bridge.watch_coordinator.tick()
        assert len(mock_rest.sent_messages) == 1
        live_card = mock_rest.sent_messages[0]
        stop_button = live_card["components"][0]["components"][0]
        assert stop_button["label"] == "Parar sessão"

        # 2. Click [Parar sessão] on Discord
        stop_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_stop_e2e",
                "token": "tok_stop_e2e",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": live_card["id"], "embeds": live_card["embeds"]},
                "data": {"component_type": 2, "custom_id": stop_button["custom_id"]},
            },
        )
        assert stop_res is not None
        assert stop_res["status"] == "executed"
        assert stop_res["action_kind"] == "stop_session"

        # Verify target conversation turn was cancelled
        conv_after_cancel = chat_store.get(target_cid, user_id)
        assert conv_after_cancel["state"] in {"cancelled", "cancelling"}

        # Verify watch in DB is updated to cancelled & notified_terminal=True
        watch_after_cancel = discord_store.get_watch_by_turn(user_id, target_cid, target_turn_id)
        assert watch_after_cancel is not None
        assert watch_after_cancel.status == "cancelled"
        assert watch_after_cancel.notified_terminal is True

        # Verify Live Progress Card on Discord was edited to cancelled (⏹ Cancelado, COLOR_CANCELLED)
        assert len(mock_rest.edited_messages) >= 1
        cancelled_card_edit = mock_rest.edited_messages[-1]
        assert cancelled_card_edit["id"] == live_card["id"]
        assert cancelled_card_edit["embeds"][0]["color"] == COLOR_CANCELLED
        assert "⏹" in cancelled_card_edit["embeds"][0]["title"]
        assert cancelled_card_edit["components"][0]["components"][0]["disabled"] is True
        assert "Sessão interrompida" in cancelled_card_edit["components"][0]["components"][0]["label"]

        # Subsequent tick does not trigger a wakeup turn for user-stopped session
        await bridge.watch_coordinator.tick()
        assert wakeup_called is False
    finally:
        await bridge.stop()
