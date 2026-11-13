"""Unit tests for interactive AskUserQuestion on Discord and Discord voice/audio transcription."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.tools.shell.confirmation import parse_answer_blocks
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.discord import (
    AGENT_ANSWERS_HEADER,
    DiscordAudioTranscriber,
    DiscordAudioTranscriptionError,
    DiscordBridgeService,
    DiscordRuntimeStatus,
    PostgresDiscordStore,
)
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter
from agentos.persistence.postgres.schema import metadata, provider_api_keys, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.projects import PostgresProjectStore
from agentos.uploads.staging import UploadStaging

import asyncio


def _make_cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher("test-master-secret-key-for-discord-bridge-0060")


class _FakeRestClient:
    def __init__(self, bot_token: str = "fake_token") -> None:
        self.bot_token = bot_token
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.acked_interactions: list[dict[str, Any]] = []
        self.edited_interaction_originals: list[dict[str, Any]] = []
        self.typing_channels: list[str] = []
        self.attachments_by_url: dict[str, bytes] = {}
        self._msg_seq = 200000000000000000
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


OGG_OPUS_SAMPLE_BYTES = (
    b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04"
    b"\x00\x00\x00\x00\x11\x22\x33\x44\x01\x13OpusHead\x01\x01\x38\x01"
    b"\x80\xbb\x00\x00\x00\x00\x00"
)
MP3_ID3_SAMPLE_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x10TIT2\x00\x00\x00\x04\x00\x00\x00test\xff\xfb\x90\x64"


@pytest.fixture
def env(tmp_path: Path) -> Any:
    db_file = tmp_path / "discord_ask_audio.db"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    metadata.create_all(engine)

    cipher = _make_cipher()
    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret-ask-audio")
    chat_store = PostgresChatStore(engine, activity_store)
    chat_app = ChatApplication(chat_store, ExecutionApplicationAdapter(engine))
    discord_store = PostgresDiscordStore(engine, cipher)
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
            "cipher": cipher,
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


def _emit_in_turn_ask_user(
    chat_store: PostgresChatStore,
    claimed_turn: dict[str, Any],
    questions: list[dict[str, Any]],
    *,
    agent_id: str = "main",
    parent_agent_id: str | None = None,
) -> None:
    chat_store.record(
        claimed_turn,
        AgentActivityEventType.TOOL_FINISHED,
        "Aguardando resposta",
        {
            "tool": "ask_user",
            "tool_name": "AskUserQuestion",
            "status": "succeeded",
            "wait_for_user": True,
            "questions": questions,
        },
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )
    chat_store.record(
        claimed_turn,
        AgentActivityEventType.TURN_WAITING_USER,
        "Aguardando sua resposta",
        {"in_turn": True, "state": "waiting_user_in_turn"},
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )


# ----------------------------------------------------------------------
# 1. Live In-Turn Code AskUserQuestion + >400 Events Regression Fix
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_live_in_turn_code_ask_user_buttons_enqueues_answer_and_handles_over_400_events(
    env: dict[str, Any],
) -> None:
    user_id = "user-carlos"
    allowed_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    discord_store: PostgresDiscordStore = env["discord_store"]
    chat_store: PostgresChatStore = env["chat_store"]
    chat_app: ChatApplication = env["chat_app"]

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_ask1",
        allowed_discord_user_ids=[allowed_uid],
        default_notify_channel_id=dm_channel_id,
    )

    friend_conv = chat_store.ensure_friend_conversation(user_id)
    friend_cid = str(friend_conv["conversation_id"])

    code_receipt = chat_store.create(
        user_id=user_id,
        message="Implemente o módulo de autenticação.",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="code_ask_in_turn_1",
        code_mode="code",
    )
    code_cid = code_receipt.conversation_id
    turn_id = code_receipt.turn_id
    claimed = chat_store.claim(turn_id)
    assert claimed is not None

    discord_store.upsert_watch(
        user_id,
        code_cid,
        turn_id,
        friend_conversation_id=friend_cid,
        discord_channel_id=dm_channel_id,
        origin="friend",
    )

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=chat_app,
        uploads=env["uploads"],
        workspace_root=env["workspaces_root"],
        local_workspaces=env["local_workspaces"],
        project_store=env["project_store"],
        rest_client_factory=env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
    )

    await bridge.start()
    try:
        rest: _FakeRestClient = env["fake_rest_clients"]["active"]

        # Emit 455 prior tool events so total events exceed the old 400-event ASC cap
        for idx in range(455):
            chat_store.record(
                claimed,
                AgentActivityEventType.TOOL_FINISHED,
                f"Leu arquivo {idx}",
                {"tool": "read_file", "tool_name": "Read", "status": "succeeded"},
            )

        # Subagent ask_user events must NOT trigger a Decision Card on Discord
        _emit_in_turn_ask_user(
            chat_store,
            claimed,
            [
                {
                    "id": "sub_q",
                    "header": "Subagente",
                    "question": "Pergunta interna de subagente?",
                    "mode": "single_choice",
                    "options": [{"id": "sim", "label": "Sim"}],
                }
            ],
            agent_id="sub-1",
            parent_agent_id="main",
        )

        await bridge.watch_coordinator.tick()
        # Only the Live Progress Card was sent; no Decision Card for subagent
        assert len(rest.sent_messages) == 1
        assert "🔄" in rest.sent_messages[0]["embeds"][0]["title"]

        # Now main agent calls AskUserQuestion in-turn (while turn.state remains "running")
        q1_text = "Qual banco de dados devemos usar para sessões?"
        _emit_in_turn_ask_user(
            chat_store,
            claimed,
            [
                {
                    "id": "db_choice",
                    "header": "Banco",
                    "question": q1_text,
                    "mode": "single_choice",
                    "options": [
                        {"id": "sqlite", "label": "SQLite", "description": "Local em arquivo"},
                        {"id": "postgres", "label": "PostgreSQL", "description": "Produção escalável"},
                        {"id": "redis", "label": "Redis", "description": "Cache em memória"},
                    ],
                }
            ],
        )

        await bridge.watch_coordinator.tick()
        assert len(rest.sent_messages) == 2
        decision_card = rest.sent_messages[1]
        assert "❓" in decision_card["embeds"][0]["title"]
        assert q1_text in decision_card["embeds"][0]["description"]

        # Verify ActionRow has 3 interactive Buttons (type=2)
        buttons = decision_card["components"][0]["components"]
        assert len(buttons) == 3
        assert [b["type"] for b in buttons] == [2, 2, 2]
        assert [b["label"] for b in buttons] == ["SQLite", "PostgreSQL", "Redis"]

        # Click "PostgreSQL" on Discord
        click_res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_ask_in_turn_1",
                "token": "tok_ask_in_turn_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": decision_card["id"], "embeds": decision_card["embeds"]},
                "data": {"component_type": 2, "custom_id": buttons[1]["custom_id"]},
            },
        )
        assert click_res is not None
        assert click_res["status"] == "executed"
        assert click_res["action_kind"] == "ask_user"

        # Turn must STILL be the same single running turn (no deadlocked 2nd turn created!)
        conv_state = chat_store.get(code_cid, user_id)
        assert conv_state["state"] == "running"
        assert len(conv_state["turns"]) == 1

        # Answer was enqueued into conversation_input_queue in canonical RuntimeToolExecutor format
        queued_items = chat_store.claim_queued_inputs(claimed)
        assert len(queued_items) == 1
        queued_text = str(queued_items[0]["content"])
        assert queued_text.startswith(AGENT_ANSWERS_HEADER)
        parsed = dict(parse_answer_blocks(queued_text))
        assert parsed == {q1_text: "PostgreSQL"}

        # Decision card on Discord was edited to show disabled confirmation
        assert rest.edited_messages[-1]["id"] == decision_card["id"]
        disabled_btn = rest.edited_messages[-1]["components"][0]["components"][0]
        assert disabled_btn["disabled"] is True
        assert "PostgreSQL" in disabled_btn["label"]

        # Simulate runtime consuming the answer and later asking a SECOND question in the same turn
        chat_store.record(
            claimed,
            AgentActivityEventType.TURN_STARTED,
            "Mensagem da fila entregue ao agente",
            {"state": "queued_input", "queued_text": queued_text},
        )
        q2_text = "Deseja habilitar migração automática no startup?"
        _emit_in_turn_ask_user(
            chat_store,
            claimed,
            [
                {
                    "id": "auto_migrate",
                    "header": "Migração",
                    "question": q2_text,
                    "mode": "single_choice",
                    "options": [
                        {"id": "sim", "label": "Sim"},
                        {"id": "nao", "label": "Não"},
                    ],
                }
            ],
        )

        await bridge.watch_coordinator.tick()
        assert len(rest.sent_messages) == 3
        second_decision_card = rest.sent_messages[2]
        assert q2_text in second_decision_card["embeds"][0]["description"]
    finally:
        await bridge.stop()


# ----------------------------------------------------------------------
# 2. Auto-Discovery of Unwatched Local PC Code Sessions on AskUserQuestion
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_auto_discovers_unwatched_local_pc_code_session_only_when_blocked(
    env: dict[str, Any],
) -> None:
    user_id = "user-carlos"
    allowed_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    discord_store: PostgresDiscordStore = env["discord_store"]
    chat_store: PostgresChatStore = env["chat_store"]
    chat_app: ChatApplication = env["chat_app"]

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_auto",
        allowed_discord_user_ids=[allowed_uid],
        default_notify_channel_id=dm_channel_id,
    )

    # Local Code session started directly on the user's PC (no Discord watch registered)
    local_receipt = chat_store.create(
        user_id=user_id,
        message="Corrija o webhook de pagamentos.",
        provider="openai-codex",
        model_id="gpt-5.3-codex",
        idempotency_key="code_local_pc_auto_1",
        code_mode="code",
    )
    local_cid = local_receipt.conversation_id
    turn_id = local_receipt.turn_id
    claimed = chat_store.claim(turn_id)
    assert claimed is not None

    friend_wakeups: list[str] = []

    async def turn_executor(cid: str, tid: str) -> None:
        conv = chat_store.get(cid, user_id)
        if conv.get("mode") == "friend":
            friend_wakeups.append(tid)

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=chat_app,
        uploads=env["uploads"],
        workspace_root=env["workspaces_root"],
        local_workspaces=env["local_workspaces"],
        project_store=env["project_store"],
        rest_client_factory=env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
        turn_executor=turn_executor,
    )

    await bridge.start()
    try:
        rest: _FakeRestClient = env["fake_rest_clients"]["active"]

        # 1. Normal running local PC session does NOT spam Discord Progress Cards
        chat_store.record(
            claimed,
            AgentActivityEventType.TOOL_FINISHED,
            "Editou webhook.py",
            {"tool": "edit_file", "file_path": "src/webhook.py", "status": "succeeded"},
        )
        await bridge.watch_coordinator.tick()
        assert rest.sent_messages == []

        # 2. Local PC session calls AskUserQuestion -> auto-discovered ("code_auto") & Decision Card sent
        question_text = "Qual ambiente usar para validar a assinatura HMAC?"
        _emit_in_turn_ask_user(
            chat_store,
            claimed,
            [
                {
                    "id": "env_q",
                    "header": "Ambiente",
                    "question": question_text,
                    "mode": "single_choice",
                    "options": [
                        {"id": "sandbox", "label": "Sandbox"},
                        {"id": "staging", "label": "Staging"},
                    ],
                }
            ],
        )

        await bridge.watch_coordinator.tick()
        # Exactly 1 message sent: the Decision Card (no unsolicited Progress Card)
        assert len(rest.sent_messages) == 1
        decision_card = rest.sent_messages[0]
        assert "❓" in decision_card["embeds"][0]["title"]
        watch = discord_store.get_watch_by_turn(user_id, local_cid, turn_id)
        assert watch is not None
        assert watch.origin == "code_auto"

        # 3. Clicking "Sandbox" on Discord answers the local PC Code session
        sandbox_btn = decision_card["components"][0]["components"][0]
        res = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_local_pc_1",
                "token": "tok_local_pc_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": decision_card["id"], "embeds": decision_card["embeds"]},
                "data": {"component_type": 2, "custom_id": sandbox_btn["custom_id"]},
            },
        )
        assert res is not None
        assert res["status"] == "executed"

        queued = chat_store.claim_queued_inputs(claimed)
        assert len(queued) == 1
        assert dict(parse_answer_blocks(str(queued[0]["content"]))) == {question_text: "Sandbox"}

        # 4. Local PC session completes -> watch closes silently without unsolicited Friend LLM wakeup
        chat_store.delta(claimed, "Validação concluída em Sandbox.")
        chat_store.finish(claimed)
        await bridge.watch_coordinator.tick()

        closed_watch = discord_store.get_watch_by_turn(user_id, local_cid, turn_id)
        assert closed_watch is not None
        assert closed_watch.status == "completed"
        assert closed_watch.notified_terminal is True
        assert friend_wakeups == []
    finally:
        await bridge.stop()


# ----------------------------------------------------------------------
# 3. Multi-Select (checkbox) and Multi-Question Batch Decision Cards
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_multi_select_checkbox_and_multi_question_batch_decision_cards(
    env: dict[str, Any],
) -> None:
    user_id = "user-carlos"
    allowed_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    discord_store: PostgresDiscordStore = env["discord_store"]
    chat_store: PostgresChatStore = env["chat_store"]
    chat_app: ChatApplication = env["chat_app"]

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_multi",
        allowed_discord_user_ids=[allowed_uid],
        default_notify_channel_id=dm_channel_id,
    )

    friend_conv = chat_store.ensure_friend_conversation(user_id)
    friend_cid = str(friend_conv["conversation_id"])

    code_receipt = chat_store.create(
        user_id=user_id,
        message="Configure os serviços.",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="code_multi_select_1",
        code_mode="code",
    )
    code_cid = code_receipt.conversation_id
    turn_id = code_receipt.turn_id
    claimed = chat_store.claim(turn_id)
    assert claimed is not None

    discord_store.upsert_watch(
        user_id,
        code_cid,
        turn_id,
        friend_conversation_id=friend_cid,
        discord_channel_id=dm_channel_id,
        origin="friend",
    )

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=chat_app,
        uploads=env["uploads"],
        workspace_root=env["workspaces_root"],
        rest_client_factory=env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
    )

    await bridge.start()
    try:
        rest: _FakeRestClient = env["fake_rest_clients"]["active"]

        # Part A: Single question with mode="checkbox" (multiSelect)
        q_checkbox = "Quais integrações devemos habilitar?"
        _emit_in_turn_ask_user(
            chat_store,
            claimed,
            [
                {
                    "id": "integrations",
                    "header": "Integrações",
                    "question": q_checkbox,
                    "mode": "checkbox",
                    "options": [
                        {"id": "stripe", "label": "Stripe"},
                        {"id": "discord", "label": "Discord"},
                        {"id": "sentry", "label": "Sentry"},
                    ],
                }
            ],
        )

        await bridge.watch_coordinator.tick()
        checkbox_card = rest.sent_messages[-1]
        select_comp = checkbox_card["components"][0]["components"][0]
        assert select_comp["type"] == 3
        assert select_comp["min_values"] == 1
        assert select_comp["max_values"] == 3

        res_multi = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_checkbox_1",
                "token": "tok_checkbox_1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": checkbox_card["id"], "embeds": checkbox_card["embeds"]},
                "data": {
                    "component_type": 3,
                    "custom_id": select_comp["custom_id"],
                    "values": [
                        select_comp["options"][0]["value"],
                        select_comp["options"][2]["value"],
                    ],
                },
            },
        )
        assert res_multi is not None
        assert res_multi["status"] == "executed"

        claimed_multi = chat_store.claim_queued_inputs(claimed)
        assert len(claimed_multi) == 1
        assert dict(parse_answer_blocks(str(claimed_multi[0]["content"]))) == {
            q_checkbox: "Stripe, Sentry"
        }
        chat_store.record(
            claimed,
            AgentActivityEventType.TURN_STARTED,
            "Mensagem da fila entregue ao agente",
            {"state": "queued_input", "queued_text": str(claimed_multi[0]["content"])},
        )

        # Part B: Multi-question batch (2 questions in one AskUserQuestion call)
        q_batch_1 = "Qual banco principal usar?"
        q_batch_2 = "Qual estratégia de deploy adotar?"
        _emit_in_turn_ask_user(
            chat_store,
            claimed,
            [
                {
                    "id": "q_db",
                    "header": "Banco",
                    "question": q_batch_1,
                    "mode": "single_choice",
                    "options": [
                        {"id": "pg", "label": "PostgreSQL"},
                        {"id": "mysql", "label": "MySQL"},
                    ],
                },
                {
                    "id": "q_deploy",
                    "header": "Deploy",
                    "question": q_batch_2,
                    "mode": "single_choice",
                    "options": [
                        {"id": "docker", "label": "Docker Compose"},
                        {"id": "k8s", "label": "Kubernetes"},
                    ],
                },
            ],
        )

        await bridge.watch_coordinator.tick()
        batch_card = rest.sent_messages[-1]
        assert len(batch_card["components"]) == 2
        row0_select = batch_card["components"][0]["components"][0]
        row1_select = batch_card["components"][1]["components"][0]
        assert row0_select["type"] == 3
        assert row1_select["type"] == 3

        # Answer Question 1 only -> partial state: updates card, does NOT enqueue yet
        res_q1 = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_batch_q1",
                "token": "tok_batch_q1",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": batch_card["id"], "embeds": batch_card["embeds"]},
                "data": {
                    "component_type": 3,
                    "custom_id": row0_select["custom_id"],
                    "values": ["opt_0"],
                },
            },
        )
        assert res_q1 is not None
        assert res_q1["status"] == "partial"
        assert chat_store.claim_queued_inputs(claimed) == []

        partial_edit = rest.edited_messages[-1]
        assert partial_edit["id"] == batch_card["id"]
        assert partial_edit["components"][0]["components"][0]["disabled"] is True
        assert "PostgreSQL" in partial_edit["components"][0]["components"][0]["placeholder"]
        assert partial_edit["components"][1]["components"][0].get("disabled") is not True

        # Answer Question 2 -> completes batch and enqueues combined Q&A blocks!
        res_q2 = await bridge.handle_interaction_create(
            user_id,
            {
                "id": "inter_batch_q2",
                "token": "tok_batch_q2",
                "channel_id": dm_channel_id,
                "user": {"id": allowed_uid},
                "message": {"id": batch_card["id"], "embeds": batch_card["embeds"]},
                "data": {
                    "component_type": 3,
                    "custom_id": row1_select["custom_id"],
                    "values": ["opt_0"],
                },
            },
        )
        assert res_q2 is not None
        assert res_q2["status"] == "executed"

        batch_queued = chat_store.claim_queued_inputs(claimed)
        assert len(batch_queued) == 1
        assert dict(parse_answer_blocks(str(batch_queued[0]["content"]))) == {
            q_batch_1: "PostgreSQL",
            q_batch_2: "Docker Compose",
        }
    finally:
        await bridge.stop()


# ----------------------------------------------------------------------
# 4. ProjectTools.manage_session(inspect & respond) for Live In-Turn AskUserQuestion
# ----------------------------------------------------------------------


def test_project_tools_manage_session_inspect_and_respond_live_in_turn(
    env: dict[str, Any],
) -> None:
    user_id = "user-carlos"
    chat_store: PostgresChatStore = env["chat_store"]
    chat_app: ChatApplication = env["chat_app"]

    friend_conv = chat_store.ensure_friend_conversation(user_id)
    friend_ws = ConversationWorkspace(env["workspaces_root"], str(friend_conv["conversation_id"]))
    toolset = AgentToolset(
        friend_ws,
        code_mode_active=False,
        conversation_id=str(friend_conv["conversation_id"]),
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
        chat_store=chat_store,
        project_store=env["project_store"],
        local_workspaces=env["local_workspaces"],
        project_user_id=user_id,
    )

    code_receipt = chat_store.create(
        user_id=user_id,
        message="Ajuste a invalidação de cache.",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="code_project_tools_1",
        code_mode="code",
    )
    code_cid = code_receipt.conversation_id
    claimed = chat_store.claim(code_receipt.turn_id)
    assert claimed is not None

    question = "Qual TTL padrão devemos aplicar no Redis?"
    _emit_in_turn_ask_user(
        chat_store,
        claimed,
        [
            {
                "id": "ttl_q",
                "header": "TTL",
                "question": question,
                "mode": "single_choice",
                "options": [
                    {"id": "5m", "label": "5 minutos"},
                    {"id": "1h", "label": "1 hora"},
                ],
            }
        ],
    )

    inspected = toolset.manage_session(action="inspect", conversation_id=code_cid).payload
    assert inspected["state"] == "waiting_user"
    assert inspected["pending_block"] is not None
    assert inspected["pending_block"]["kind"] == "ask_user"
    assert inspected["pending_block"]["in_turn"] is True

    responded = toolset.manage_session(
        action="respond",
        conversation_id=code_cid,
        message="1 hora",
    )
    assert responded.status == "succeeded"
    assert responded.payload["queued_in_turn"] is True

    queued = chat_store.claim_queued_inputs(claimed)
    assert len(queued) == 1
    assert dict(parse_answer_blocks(str(queued[0]["content"]))) == {question: "1 hora"}


# ----------------------------------------------------------------------
# 5. DiscordAudioTranscriber Cascade (Local Whisper -> Cloud STT -> Agy)
# ----------------------------------------------------------------------


def _seed_encrypted_provider_key(
    engine: Any,
    cipher: ProviderSecretCipher,
    *,
    user_id: str,
    provider: str,
    raw_key: str,
    base_url: str | None = None,
) -> None:
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id=user_id,
                provider=provider,
                enabled=True,
                base_url=base_url,
                secret_ref=f"secret:{provider}",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                risk_acknowledged_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(provider_api_keys).values(
                user_id=user_id,
                provider=provider,
                label="Chave STT Teste",
                api_key_ciphertext=cipher.encrypt(raw_key),
                secret_ref=f"secret:{uuid4().hex[:8]}",
                position=0,
                status="active",
                cooldown_until=None,
                created_at=now,
                updated_at=now,
            )
        )


def test_discord_audio_transcriber_cascade_and_fallbacks(
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    user_id = "user-stt"
    audio_file = tmp_path / "voice-note.opus"
    audio_file.write_bytes(OGG_OPUS_SAMPLE_BYTES)

    # 1. Local Whisper succeeds -> immediate return without touching cloud or Agy
    class _WorkingWhisper:
        def is_installed(self) -> bool:
            return True

        def transcribe_file(self, path: str | Path, *, language: str | None = None) -> str:
            assert Path(path).suffix == ".opus"
            assert Path(path).read_bytes() == OGG_OPUS_SAMPLE_BYTES
            assert language == "pt"
            return "Fala transcrita pelo Whisper local"

    t_local = DiscordAudioTranscriber(
        engine=env["engine"],
        secret_cipher=env["cipher"],
        whisper_engine=_WorkingWhisper(),  # type: ignore[arg-type]
        agy_transcriber_factory=lambda: (_ for _ in ()).throw(AssertionError("Should not call Agy")),
    )
    res_local = t_local.transcribe_audio(
        user_id=user_id,
        audio_bytes=OGG_OPUS_SAMPLE_BYTES,
        filename="voice-note.opus",
        content_type="audio/opus",
        language="pt",
        audio_path=audio_file,
    )
    assert res_local == "Fala transcrita pelo Whisper local"

    # 2. Local Whisper unavailable -> Cloud OpenAI STT uses encrypted DB key and normalizes .opus -> .ogg
    class _FailingWhisper:
        def is_installed(self) -> bool:
            return False

        def transcribe_file(self, _path: str | Path, *, language: str | None = None) -> str:
            raise RuntimeError(f"faster-whisper ausente ({language})")

    _seed_encrypted_provider_key(
        env["engine"],
        env["cipher"],
        user_id=user_id,
        provider="openai",
        raw_key="sk-openai-encrypted-test-key",
    )

    seen_openai_requests: list[httpx.Request] = []

    def _openai_handler(request: httpx.Request) -> httpx.Response:
        seen_openai_requests.append(request)
        assert request.headers["Authorization"] == "Bearer sk-openai-encrypted-test-key"
        # Verify .opus filename was normalized to .ogg for OpenAI STT multipart upload
        assert b'filename="voice-note.ogg"' in request.content
        return httpx.Response(200, json={"text": "Áudio transcrito via OpenAI Whisper"})

    t_cloud = DiscordAudioTranscriber(
        engine=env["engine"],
        secret_cipher=env["cipher"],
        whisper_engine=_FailingWhisper(),  # type: ignore[arg-type]
        http_transport=httpx.MockTransport(_openai_handler),
        agy_transcriber_factory=lambda: None,  # type: ignore[return-value]
    )
    res_cloud = t_cloud.transcribe_audio(
        user_id=user_id,
        audio_bytes=OGG_OPUS_SAMPLE_BYTES,
        filename="voice-note.opus",
        content_type="audio/opus",
    )
    assert res_cloud == "Áudio transcrito via OpenAI Whisper"
    assert len(seen_openai_requests) == 1

    # 3. OpenRouter JSON input_audio fallback when user only has OpenRouter configured
    user_or = "user-openrouter-only"
    _seed_encrypted_provider_key(
        env["engine"],
        env["cipher"],
        user_id=user_or,
        provider="openrouter",
        raw_key="sk-or-v1-encrypted-test",
    )

    def _openrouter_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-or-v1-encrypted-test"
        body = json.loads(request.content.decode("utf-8"))
        assert body["input_audio"]["format"] == "ogg"
        return httpx.Response(
            200,
            json={"text": "Transcrição via OpenRouter multimodal"},
        )

    t_or = DiscordAudioTranscriber(
        engine=env["engine"],
        secret_cipher=env["cipher"],
        whisper_engine=_FailingWhisper(),  # type: ignore[arg-type]
        http_transport=httpx.MockTransport(_openrouter_handler),
        agy_transcriber_factory=lambda: None,  # type: ignore[return-value]
    )
    res_or = t_or.transcribe_audio(
        user_id=user_or,
        audio_bytes=OGG_OPUS_SAMPLE_BYTES,
        filename="voice-note.ogg",
        content_type="audio/ogg",
    )
    assert res_or == "Transcrição via OpenRouter multimodal"

    # 4. Agy fallback when neither local Whisper nor cloud keys exist
    class _FakeAgyTranscriber:
        def available(self) -> bool:
            return True

        def transcribe(self, path: Path, *, kind: str) -> str:
            assert Path(path).suffix == ".opus"
            assert Path(path).read_bytes() == OGG_OPUS_SAMPLE_BYTES
            assert kind == "audio"
            return "Transcrição via Antigravity CLI"

    user_agy = "user-agy-only"
    now = datetime.now(UTC)
    with env["engine"].begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id=user_agy,
                provider="google-antigravity",
                enabled=True,
                base_url=None,
                secret_ref="secret:google-antigravity",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                risk_acknowledged_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    t_agy = DiscordAudioTranscriber(
        engine=env["engine"],
        secret_cipher=env["cipher"],
        whisper_engine=_FailingWhisper(),  # type: ignore[arg-type]
        agy_transcriber_factory=_FakeAgyTranscriber,  # type: ignore[arg-type]
    )
    res_agy = t_agy.transcribe_audio(
        user_id=user_agy,
        audio_bytes=OGG_OPUS_SAMPLE_BYTES,
        filename="voice-note.opus",
        audio_path=audio_file,
    )
    assert res_agy == "Transcrição via Antigravity CLI"

    # 5. All backends fail -> raises DiscordAudioTranscriptionError
    class _UnavailableAgy:
        def available(self) -> bool:
            return False

    t_none = DiscordAudioTranscriber(
        engine=env["engine"],
        secret_cipher=env["cipher"],
        whisper_engine=_FailingWhisper(),  # type: ignore[arg-type]
        agy_transcriber_factory=_UnavailableAgy,  # type: ignore[arg-type]
    )
    with pytest.raises(DiscordAudioTranscriptionError):
        t_none.transcribe_audio(
            user_id="user-no-keys",
            audio_bytes=OGG_OPUS_SAMPLE_BYTES,
            filename="voice-note.ogg",
        )


# ----------------------------------------------------------------------
# 6. DiscordBridgeService Voice Message & Audio Attachment Handling
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_bridge_transcribes_discord_voice_message_and_audio_attachments(
    env: dict[str, Any],
) -> None:
    user_id = "user-carlos"
    allowed_uid = "123456789012345678"
    dm_channel_id = "222333444555666777"

    discord_store: PostgresDiscordStore = env["discord_store"]
    chat_store: PostgresChatStore = env["chat_store"]

    discord_store.save_config(
        user_id,
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_voice",
        allowed_discord_user_ids=[allowed_uid],
    )

    transcribed_filenames: list[str] = []
    should_fail_transcription = False

    class _StubAudioTranscriber:
        def transcribe_audio(
            self,
            *,
            user_id: str,
            audio_bytes: bytes,
            filename: str = "voice-message.ogg",
            content_type: str | None = None,
            audio_path: Path | None = None,
        ) -> str:
            _ = (user_id, audio_bytes, content_type, audio_path)
            transcribed_filenames.append(filename)
            if should_fail_transcription:
                raise DiscordAudioTranscriptionError("Nenhum motor STT configurado")
            if filename.endswith(".ogg"):
                return "Orin, verifica como está a fila de e-mails do pós-vendas hoje."
            return "Segue também o áudio explicando o erro de login."

    seen_friend_prompts: list[str] = []

    async def fake_turn_executor(conversation_id: str, turn_id: str) -> None:
        claimed = chat_store.claim(turn_id)
        assert claimed is not None
        assert claimed["live_mode"] is False
        conv = chat_store.get(conversation_id, user_id)
        user_msgs = [m for m in conv["messages"] if m["role"] == "user"]
        seen_friend_prompts.append(str(user_msgs[-1]["content"]))
        chat_store.delta(claimed, "Ouvi seu áudio! Vou checar a fila de e-mails do pós-vendas agora.")
        chat_store.finish(claimed)

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=env["chat_app"],
        uploads=env["uploads"],
        workspace_root=env["workspaces_root"],
        rest_client_factory=env["rest_factory"],
        gateway_client_factory=_FakeGatewayClient,
        turn_executor=fake_turn_executor,
        audio_transcriber=_StubAudioTranscriber(),  # type: ignore[arg-type]
    )

    await bridge.start()
    try:
        rest: _FakeRestClient = env["fake_rest_clients"]["active"]
        voice_url = "https://cdn.discordapp.com/attachments/111/222/voice-message.ogg"
        mp3_url = "https://cdn.discordapp.com/attachments/111/222/detalhe.mpga"
        rest.attachments_by_url[voice_url] = OGG_OPUS_SAMPLE_BYTES
        rest.attachments_by_url[mp3_url] = MP3_ID3_SAMPLE_BYTES

        # Case 1: Native Discord Voice Message (flags=8192 IS_VOICE_MESSAGE, empty content)
        res_voice = await bridge.handle_message_create(
            user_id,
            {
                "id": "500000000000000001",
                "channel_id": dm_channel_id,
                "author": {"id": allowed_uid},
                "flags": 8192,
                "content": "",
                "attachments": [
                    {
                        "id": "att_voice_1",
                        "filename": "voice-message.ogg",
                        "content_type": "audio/ogg",
                        "url": voice_url,
                        "size": len(OGG_OPUS_SAMPLE_BYTES),
                        "duration_secs": 4.2,
                        "waveform": "AAECAwQF",
                    }
                ],
            },
        )
        assert res_voice is not None
        assert dm_channel_id in rest.typing_channels
        assert len(transcribed_filenames) == 1
        assert seen_friend_prompts[-1] == (
            "[Mensagem de voz enviada pelo usuário no Discord — transcrição]:\n"
            "Orin, verifica como está a fila de e-mails do pós-vendas hoje."
        )
        assert "Ouvi seu áudio!" in (rest.sent_messages[-1]["content"] or "")

        # Case 2: Text message + .mpga audio attachment (normalized to .mp3)
        res_combo = await bridge.handle_message_create(
            user_id,
            {
                "id": "500000000000000002",
                "channel_id": dm_channel_id,
                "author": {"id": allowed_uid},
                "content": "Olha esse relato do cliente:",
                "attachments": [
                    {
                        "id": "att_mp3_1",
                        "filename": "detalhe.mpga",
                        "content_type": "audio/mpeg",
                        "url": mp3_url,
                        "size": len(MP3_ID3_SAMPLE_BYTES),
                    }
                ],
            },
        )
        assert res_combo is not None
        assert len(transcribed_filenames) == 2
        assert transcribed_filenames[-1] == "detalhe.mp3"
        assert "Olha esse relato do cliente:" in seen_friend_prompts[-1]
        assert "Segue também o áudio explicando o erro de login." in seen_friend_prompts[-1]

        # Case 3: Voice-only message when STT fails -> reports error on Discord instead of generic image prompt
        should_fail_transcription = True
        prev_prompt_count = len(seen_friend_prompts)
        res_err = await bridge.handle_message_create(
            user_id,
            {
                "id": "500000000000000003",
                "channel_id": dm_channel_id,
                "author": {"id": allowed_uid},
                "flags": 8192,
                "content": "",
                "attachments": [
                    {
                        "id": "att_voice_fail",
                        "filename": "voice-message.ogg",
                        "content_type": "audio/ogg",
                        "url": voice_url,
                        "size": len(OGG_OPUS_SAMPLE_BYTES),
                    }
                ],
            },
        )
        assert res_err is None
        assert len(seen_friend_prompts) == prev_prompt_count
        assert "Não foi possível transcrever o áudio" in (rest.sent_messages[-1]["content"] or "")
    finally:
        await bridge.stop()
