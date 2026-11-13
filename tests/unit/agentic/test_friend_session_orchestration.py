from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.phases import PHASE_TOOLS, Phase
from agentos.agentic.prompts import FRIEND_SYSTEM_PROMPT_SECTION, LIVE_MODE_SPEAK_GUIDANCE
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.session import TurnSession, build_system_prompt
from agentos.agentic.tools.public_names import public_name
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.permissions import ToolPermissionBroker
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.conversations.code_milestone_watcher import CodeSessionMilestoneWatcherRegistry
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.memory.friend_persona import FriendPersonaStore
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import conversation_activity_events, metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.projects.store import PostgresProjectStore
from agentos.provider_catalog.models import PricingSummary, ProviderCatalogContext, ProviderModelRecord


def _make_engine(tmp_path: Path | None = None):
    if tmp_path is not None:
        engine = create_engine(f"sqlite:///{tmp_path / 'phase1_test.db'}", future=True)
    else:
        engine = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    metadata.create_all(engine)
    return engine


class _DummyAgents:
    def create(self, name: str, role: str, **kwargs: object) -> dict[str, object]:
        return {"agent_id": "agent-1", "name": name, "role": role, "created": True}

    def find(self, name: str) -> dict[str, object] | None:
        return None

    def list(self) -> list[dict[str, object]]:
        return []

    def set_state(self, agent_id: str, state: str) -> None:
        pass

    def record_usage(self, *args: object, **kwargs: object) -> None:
        pass


def test_list_models_returns_configured_providers_favorites_capabilities_and_filters(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    cipher = ProviderSecretCipher(b"0" * 32)
    repo = PostgresProviderCatalogRepository(engine, cipher=cipher)
    now = datetime.now(UTC)
    ctx = ProviderCatalogContext(user_id="user-1", purpose="test")

    with engine.begin() as conn:
        for prov in ("anthropic", "google-antigravity", "openrouter", "ollama"):
            conn.execute(
                insert(provider_configurations).values(
                    user_id="user-1",
                    provider=prov,
                    enabled=True,
                    base_url=None,
                    secret_ref=f"secret:{prov}",
                    key_cooldown_seconds=60,
                    catalog_refreshed_at=now,
                    risk_acknowledged_at=now if prov == "google-antigravity" else None,
                    created_at=now,
                    updated_at=now,
                )
            )

    repo.replace(
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
            ProviderModelRecord(
                provider="anthropic",
                model_id="claude-opus-4-5",
                display_name="Claude Opus 4.5",
                context_window=200_000,
                capabilities=("tools", "reasoning"),
                input_modalities=("text", "image"),
                output_modalities=("text",),
                pricing=PricingSummary("15.0", "75.0"),
                refreshed_at=now,
            ),
        ],
        now,
    )
    repo.replace(
        ctx,
        "google-antigravity",
        [
            ProviderModelRecord(
                provider="google-antigravity",
                model_id="gemini-4-argon-high",
                display_name="Gemini 4 Argon High",
                context_window=1_000_000,
                capabilities=("tools", "thinking", "vision"),
                input_modalities=("text", "image"),
                output_modalities=("text",),
                pricing=None,
                refreshed_at=now,
            ),
        ],
        now,
    )
    repo.replace(
        ctx,
        "ollama",
        [
            ProviderModelRecord(
                provider="ollama",
                model_id="qwen3:8b",
                display_name="Qwen 3 8B Local",
                context_window=32_768,
                capabilities=("tools",),
                input_modalities=("text",),
                output_modalities=("text",),
                pricing=None,
                refreshed_at=now,
            ),
        ],
        now,
    )
    repo.set_favorite(ctx, "anthropic", "claude-sonnet-4-5", True)
    repo.set_favorite(ctx, "google-antigravity", "gemini-4-argon-high", True)

    chat_store = PostgresChatStore(engine)
    workspace = ConversationWorkspace(tmp_path, "conv-models")
    toolset = AgentToolset(
        workspace,
        chat_store=chat_store,
        provider_catalog=repo,
        project_user_id="user-1",
    )

    assert public_name("list_models") == "ListModels"
    assert public_name("manage_session") == "ManageSession"
    assert "list_models" in PHASE_TOOLS[Phase.ORIENT]
    assert "list_models" in PHASE_TOOLS[Phase.PLAN]
    assert "list_models" in PHASE_TOOLS[Phase.EXECUTE]
    assert "manage_session" in PHASE_TOOLS[Phase.ORIENT]
    assert "manage_session" in PHASE_TOOLS[Phase.PLAN]
    assert "manage_session" in PHASE_TOOLS[Phase.EXECUTE]

    outcome = toolset.invoke("list_models", {})
    assert outcome.status == "succeeded"
    models = outcome.payload["models"]
    by_key = {(m["provider"], m["model_id"]): m for m in models}

    assert ("anthropic", "claude-sonnet-4-5") in by_key
    sonnet = by_key[("anthropic", "claude-sonnet-4-5")]
    assert sonnet["is_favorite"] is True
    assert sonnet["kind"] == "api"
    assert "tools" in sonnet["capabilities"]
    assert "vision" in sonnet["capabilities"]
    assert "thinking" in sonnet["capabilities"]
    assert sonnet["context_window"] == 200_000
    assert sonnet["pricing"] == {"input_per_million": 3.0, "output_per_million": 15.0}

    argon = by_key[("google-antigravity", "gemini-4-argon-high")]
    assert argon["kind"] == "subscription_cli"
    assert argon["is_favorite"] is True

    qwen_local = by_key[("ollama", "qwen3:8b")]
    assert qwen_local["kind"] == "local"
    assert qwen_local["is_favorite"] is False

    # Filter: favorites_only
    fav_outcome = toolset.list_models(favorites_only=True)
    assert all(m["is_favorite"] for m in fav_outcome.payload["models"])
    assert {m["model_id"] for m in fav_outcome.payload["models"]} >= {"claude-sonnet-4-5", "gemini-4-argon-high"}
    assert "qwen3:8b" not in {m["model_id"] for m in fav_outcome.payload["models"]}

    # Filter: provider + capability + query
    vision_outcome = toolset.list_models(provider="anthropic", capability="thinking", query="opus")
    assert len(vision_outcome.payload["models"]) == 1
    assert vision_outcome.payload["models"][0]["model_id"] == "claude-opus-4-5"


def test_resolve_session_defaults_fixes_provider_override_bug(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-defaults-fix")
    catalog = [
        {
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-5",
            "display_name": "Claude Sonnet 4.5",
            "is_favorite": True,
            "capabilities": ["tools", "thinking", "vision"],
        },
        {
            "provider": "google-antigravity",
            "model_id": "gemini-4-carbon-high",
            "display_name": "Gemini 4 Carbon High",
            "is_favorite": True,
            "capabilities": ["tools", "thinking", "vision"],
        },
    ]
    toolset = AgentToolset(
        workspace,
        default_provider="openai-codex",
        default_model_id="gpt-5.3-codex",
        provider_catalog=catalog,
        project_user_id="user-1",
    )

    # 1. Both provider and model_id passed -> preserve both
    _, prov, mod, perm = toolset._resolve_session_defaults(
        workspace_path=None,
        provider="anthropic",
        model_id="claude-opus-4-5",
        permission_mode="full_autonomy",
    )
    assert prov == "anthropic"
    assert mod == "claude-opus-4-5"
    assert perm == "full_autonomy"

    # 2. Only provider passed -> do NOT revert to default_provider; resolve favorite model of that provider
    _, prov2, mod2, _ = toolset._resolve_session_defaults(
        workspace_path=None,
        provider="anthropic",
        model_id=None,
        permission_mode="accept_edits",
    )
    assert prov2 == "anthropic"
    assert mod2 == "claude-sonnet-4-5"

    # 3. Only model_id passed -> infer matching provider from catalog
    _, prov3, mod3, _ = toolset._resolve_session_defaults(
        workspace_path=None,
        provider=None,
        model_id="gemini-4-carbon-high",
        permission_mode="accept_edits",
    )
    assert prov3 == "google-antigravity"
    assert mod3 == "gemini-4-carbon-high"

    # 4. Neither passed -> fallback to default_provider and default_model_id
    _, prov4, mod4, _ = toolset._resolve_session_defaults(
        workspace_path=None,
        provider=None,
        model_id=None,
        permission_mode="accept_edits",
    )
    assert prov4 == "openai-codex"
    assert mod4 == "gpt-5.3-codex"


def test_manage_session_full_lifecycle_chat_and_code_projects_and_watch_hook(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    chat_store = PostgresChatStore(engine)
    project_store = PostgresProjectStore(engine)
    local_workspaces = PostgresLocalWorkspaceStore(engine)
    milestone_registry = CodeSessionMilestoneWatcherRegistry()

    project_dir = tmp_path / "educly_repo"
    project_dir.mkdir()
    proj = project_store.create(user_id="user-1", name="Educly App", description="App principal")
    local_workspaces.set_root(proj.workspace_id, "user-1", str(project_dir))

    friend_receipt = chat_store.create(
        user_id="user-1",
        message="Oi Orin Friend",
        provider="openai-codex",
        model_id="gpt-5.3-codex",
        idempotency_key="friend-init-1",
        code_mode="friend",
    )

    watched_calls: list[dict[str, Any]] = []
    workspace = ConversationWorkspace(tmp_path, friend_receipt.conversation_id)
    toolset = AgentToolset(
        workspace,
        code_mode_active=False,
        conversation_id=friend_receipt.conversation_id,
        default_provider="openai-codex",
        default_model_id="gpt-5.3-codex",
        chat_store=chat_store,
        project_store=project_store,
        local_workspaces=local_workspaces,
        project_user_id="user-1",
        provider_catalog=[
            {
                "provider": "anthropic",
                "model_id": "claude-sonnet-4-5",
                "display_name": "Claude Sonnet 4.5",
                "is_favorite": True,
                "capabilities": ["tools", "thinking"],
            }
        ],
        on_session_watch=lambda **kw: watched_calls.append(kw),
    )
    toolset._milestone_registry = milestone_registry

    # Start a CODE session referencing the project by name ("Educly App"), provider-only ("anthropic"), and full_autonomy
    started_code = toolset.manage_session(
        action="start",
        mode="code",
        project_id="Educly App",
        title="Corrigir fluxo de login Educly",
        prompt="Investigue auth.ts e corrija o bug de sessão expirada.",
        provider="anthropic",
        permission_mode="full_autonomy",
    )
    assert started_code.status == "succeeded"
    code_cid = started_code.payload["conversation_id"]
    assert started_code.payload["mode"] == "code"
    assert started_code.payload["project_id"] == proj.project_id
    assert started_code.payload["workspace_path"] == str(project_dir)
    assert started_code.payload["provider"] == "anthropic"
    assert started_code.payload["model_id"] == "claude-sonnet-4-5"
    assert started_code.payload["permission_mode"] == "full_autonomy"
    assert started_code.payload["title"] == "Corrigir fluxo de login Educly"
    assert len(watched_calls) == 1
    assert watched_calls[0]["target_conversation_id"] == code_cid
    assert watched_calls[0]["friend_conversation_id"] == friend_receipt.conversation_id

    # Verify the persisted title and project_id in PostgresChatStore
    persisted_code = chat_store.get(code_cid, "user-1")
    assert persisted_code["title"] == "Corrigir fluxo de login Educly"
    assert persisted_code["project_id"] == proj.project_id
    assert persisted_code["permission_mode"] == "full_autonomy"

    # Start a CHAT session in the same project
    started_chat = toolset.manage_session(
        action="start",
        mode="chat",
        project_id=proj.project_id,
        title="Análise de métricas de reembolso",
        prompt="Resuma as causas de reembolso da semana.",
        provider="openai-codex",
        model_id="gpt-5.3-codex",
    )
    assert started_chat.status == "succeeded"
    chat_cid = started_chat.payload["conversation_id"]
    assert started_chat.payload["mode"] == "chat"
    assert len(watched_calls) == 2

    # List sessions filtered by project_id and mode
    listed_all = toolset.manage_session(action="list", project_id="Educly App")
    listed_ids = {s["conversation_id"] for s in listed_all.payload["sessions"]}
    assert {code_cid, chat_cid} <= listed_ids
    assert friend_receipt.conversation_id not in listed_ids

    listed_chat = toolset.manage_session(action="list", mode="chat")
    assert any(s["conversation_id"] == chat_cid for s in listed_chat.payload["sessions"])
    assert all(s["mode"] == "chat" for s in listed_chat.payload["sessions"])

    # Send message to the CHAT session and switch model/provider
    sent_chat = toolset.manage_session(
        action="send_message",
        conversation_id=chat_cid,
        message="Inclua também o comparativo com chargeback.",
        provider="anthropic",
    )
    assert sent_chat.status == "succeeded"
    assert sent_chat.payload["provider"] == "anthropic"
    assert sent_chat.payload["model_id"] == "claude-sonnet-4-5"
    assert len(watched_calls) == 3

    # Simulate TodoWrite, file edit, and AskUserQuestion on the CODE session and inspect it
    code_turns = persisted_code["turns"]
    last_turn_id = code_turns[-1]["turn_id"]
    claimed_code = chat_store.claim(last_turn_id)
    assert claimed_code is not None
    now_evt = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_activity_events),
            [
                {
                    "event_id": f"activity:{last_turn_id}:1",
                    "conversation_id": code_cid,
                    "turn_id": last_turn_id,
                    "execution_id": str(claimed_code["execution_id"]),
                    "user_id": "user-1",
                    "agent_id": f"agent:{code_cid}:main",
                    "parent_agent_id": None,
                    "event_type": AgentActivityEventType.TOOL_FINISHED.value,
                    "sequence": 1,
                    "summary": "Atualizou lista de tarefas",
                    "payload": {
                        "tool_name": "todo_write",
                        "todos": [
                            {"content": "Ler auth.ts", "status": "completed", "activeForm": "Lendo auth.ts"},
                            {"content": "Corrigir refresh token", "status": "in_progress", "activeForm": "Corrigindo refresh token"},
                        ],
                    },
                    "visibility": "public",
                    "created_at": now_evt,
                },
                {
                    "event_id": f"activity:{last_turn_id}:2",
                    "conversation_id": code_cid,
                    "turn_id": last_turn_id,
                    "execution_id": str(claimed_code["execution_id"]),
                    "user_id": "user-1",
                    "agent_id": f"agent:{code_cid}:main",
                    "parent_agent_id": None,
                    "event_type": AgentActivityEventType.FILESYSTEM_WRITE.value,
                    "sequence": 2,
                    "summary": "Editou src/auth.ts",
                    "payload": {"tool_name": "edit_file", "path": "src/auth.ts"},
                    "visibility": "public",
                    "created_at": now_evt,
                },
                {
                    "event_id": f"activity:{last_turn_id}:3",
                    "conversation_id": code_cid,
                    "turn_id": last_turn_id,
                    "execution_id": str(claimed_code["execution_id"]),
                    "user_id": "user-1",
                    "agent_id": f"agent:{code_cid}:main",
                    "parent_agent_id": None,
                    "event_type": AgentActivityEventType.TURN_WAITING_USER.value,
                    "sequence": 3,
                    "summary": "Pergunta ao usuário",
                    "payload": {
                        "tool_kind": "user_input",
                        "wait_for_user": True,
                        "questions": [
                            {
                                "id": "token_ttl",
                                "question": "Qual TTL usar para o refresh token?",
                                "mode": "single_choice",
                                "options": [
                                    {"id": "7d", "label": "7 dias"},
                                    {"id": "30d", "label": "30 dias"},
                                ],
                            }
                        ],
                    },
                    "visibility": "public",
                    "created_at": now_evt,
                },
            ],
        )
    chat_store.finish(claimed_code, code="WAITING_USER")

    inspected = toolset.manage_session(action="inspect", conversation_id=code_cid)
    assert inspected.status == "succeeded"
    assert inspected.payload["todos_total"] == 2
    assert inspected.payload["todos_completed"] == 1
    assert "src/auth.ts" in inspected.payload["files_modified"]
    assert inspected.payload["pending_block"] is not None
    assert inspected.payload["pending_block"]["kind"] == "ask_user"
    assert inspected.payload["pending_block"]["questions"][0]["id"] == "token_ttl"

    # Respond to AskUserQuestion via manage_session(action="respond")
    answered = toolset.manage_session(
        action="respond",
        conversation_id=code_cid,
        answers={"token_ttl": "30 dias"},
    )
    assert answered.status == "succeeded"
    assert answered.payload["response_kind"] == "ask_user"

    # Create a pending ToolPermissionBroker request in the project's workspace and resolve it via manage_session
    broker = ToolPermissionBroker(project_dir)
    perm_req = broker.create_permission_request(
        tool_name="run_command",
        arguments={"command": "uv run pytest"},
        turn_id=answered.payload["turn_id"],
        conversation_id=code_cid,
    )
    inspected_perm = toolset.manage_session(action="inspect", conversation_id=code_cid)
    assert inspected_perm.payload["pending_block"] is not None
    assert inspected_perm.payload["pending_block"]["kind"] == "tool_permission"
    assert inspected_perm.payload["pending_block"]["request"]["request_id"] == perm_req["request_id"]

    resolved_perm = toolset.manage_session(
        action="respond",
        conversation_id=code_cid,
        decision="allow_once",
    )
    assert resolved_perm.status == "succeeded"
    assert resolved_perm.payload["response_kind"] == "tool_permission"
    assert resolved_perm.payload["allowed"] is True
    assert broker.get_request(perm_req["request_id"])["status"] == "resolved"

    # Stop both sessions
    stopped_code = toolset.manage_session(action="stop", conversation_id=code_cid)
    assert stopped_code.status == "succeeded"
    stopped_chat = toolset.manage_session(action="stop", conversation_id=chat_cid)
    assert stopped_chat.status == "succeeded"

    # Self-control and Friend-mode guards must reject
    with pytest.raises(AgentToolError, match="recursively"):
        toolset.manage_session(action="inspect", conversation_id=friend_receipt.conversation_id)
    with pytest.raises(AgentToolError, match="friend"):
        toolset.manage_session(action="start", mode="friend", prompt="Loop infinito")


def test_friend_live_mode_false_omits_speak_guidance_and_fallback_while_live_mode_true_preserves_it(
    tmp_path: Path,
) -> None:
    engine = _make_engine(tmp_path)
    chat_store = PostgresChatStore(engine)
    app = ChatApplication(chat_store)

    # 1. Explicit live_mode=False on a Friend conversation survives DB round-trip
    receipt_text = app.create(
        user_id="user-1",
        message="Oi Orin, me ajuda aqui pelo Discord com código Markdown",
        provider="openai",
        model_id="gpt-5.4",
        idempotency_key="friend-discord-1",
        code_mode="friend",
        live_mode=False,
    )
    # Simulate a fresh worker process by clearing the in-memory dict before claiming
    chat_store._live_mode_turns.clear()
    claimed_text = chat_store.claim(receipt_text.turn_id)
    assert claimed_text is not None
    assert claimed_text["conversation_mode"] == "friend"
    assert claimed_text["live_mode"] is False
    chat_store.finish(claimed_text)

    # 2. Default (omitted live_mode) on a Friend conversation still defaults to live_mode=True
    receipt_voice = app.send(
        user_id="user-1",
        conversation_id=receipt_text.conversation_id,
        message="Alô, está me ouvindo?",
        provider="openai",
        model_id="gpt-5.4",
        idempotency_key="friend-voice-2",
    )
    chat_store._live_mode_turns.clear()
    claimed_voice = chat_store.claim(receipt_voice.turn_id)
    assert claimed_voice is not None
    assert claimed_voice["live_mode"] is True
    chat_store.finish(claimed_voice)

    # 3. Verify TurnSession and runtime behavior for live_mode=False vs live_mode=True
    soul_dir = tmp_path / "friend_soul"
    persona_store = FriendPersonaStore(engine, soul_dir=soul_dir)
    persona_store.update_persona("user-1", current_mood="focado e prestativo", style_notes=["Seja objetivo."])
    memory_store = PostgresAgentMemoryStore(engine, user_id="user-1")
    memory_store.save("Trabalha no projeto Educly", kind="fact", scope_type="friend", salience=0.9)

    class _FakeRuntimeStore:
        def __init__(self, turn: dict[str, object], history: list[dict[str, object]]) -> None:
            self.turn = turn
            self._history = history
            self.deltas: list[str] = []
            self.activities: list[tuple[object, str, dict[str, object]]] = []

        def main_agent_id(self, _turn: dict[str, object]) -> str:
            return "agent:main"

        def history_for_turn(self, _turn: dict[str, object]) -> list[dict[str, object]]:
            return list(self._history)

        def get_turn(self, _turn_id: str) -> dict[str, object]:
            return self.turn

        def lifecycle(self, *_args: object, **_kwargs: object) -> None:
            pass

        def delta(self, _turn: dict[str, object], text: str, **_kwargs: object) -> None:
            self.deltas.append(text)

        def activity(self, _turn: dict[str, object], event_type: object, summary: str, payload: dict[str, object] | None = None) -> None:
            self.activities.append((event_type, summary, dict(payload or {})))

        def finish(self, _turn: dict[str, object], **_kwargs: object) -> None:
            pass

    class _MarkdownProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request: dict[str, object]):
            self.requests.append(request)
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TEXT,
                        1,
                        text="Aqui está o resumo das sessões:\n\n- **Educly**: rodando testes\n```python\nassert True\n```",
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    text_store = _FakeRuntimeStore(
        turn=dict(claimed_text),
        history=[
            {"role": "user", "content": "Oi"},
            {"role": "assistant", "content": "Resposta anterior em texto puro sem tags."},
            {"role": "user", "content": "Me manda o status em Markdown"},
        ],
    )
    text_provider = _MarkdownProvider()
    text_session = TurnSession(
        turn=text_store.turn,
        store=text_store,
        agents_store=_DummyAgents(),
        memory_store=memory_store,
        friend_persona_store=persona_store,
        provider_factory=lambda: text_provider,
        workspace_root=tmp_path,
        conversation_mode="friend",
    )

    assert text_session.turn["live_mode"] is False
    assert FRIEND_SYSTEM_PROMPT_SECTION.strip() in text_session._system_prompt
    assert "list_models" in text_session._system_prompt
    assert "manage_session" in text_session._system_prompt
    assert LIVE_MODE_SPEAK_GUIDANCE.strip() not in text_session._system_prompt
    assert "focado e prestativo" in text_session._volatile_prompt
    assert "Educly" in text_session._volatile_prompt

    text_runtime = text_session.build_runtime()
    assert text_runtime.phases is None

    res_text = text_session.run()
    assert res_text.state == "completed"
    assert "```python" in "".join(text_store.deltas)
    # Historical assistant message must NOT be wrapped in <speak> when live_mode=False
    sent_msgs = text_provider.requests[0]["messages"]
    asst_prev = [m for m in sent_msgs if isinstance(m, dict) and m.get("role") == "assistant"][0]
    assert "<speak>" not in str(asst_prev["content"])
    # No speak.chunk_ready fallback emitted when live_mode=False and no <speak> tags were used
    speak_events = [
        evt for evt, _s, _p in text_store.activities if evt is AgentActivityEventType.SPEAK_CHUNK_READY
    ]
    assert speak_events == []

    # Contrast with live_mode=True
    stable_live, _ = build_system_prompt(
        tool_names=("list_models", "manage_session"),
        memories=[],
        agents=[],
        workspace_hint="Hint",
        subagents_enabled=False,
        conversation_mode="friend",
        live_mode=True,
    )
    assert FRIEND_SYSTEM_PROMPT_SECTION.strip() in stable_live
    assert LIVE_MODE_SPEAK_GUIDANCE.strip() in stable_live


def test_claim_handles_multiple_queued_events_and_speak_parser_preserves_code_indentation(
    tmp_path: Path,
) -> None:
    from sqlalchemy import insert

    from agentos.agentic.speak_stream import SpeakStreamParser
    from agentos.persistence.postgres.schema import conversation_events

    engine = _make_engine(tmp_path)
    chat_store = PostgresChatStore(engine)
    app = ChatApplication(chat_store)

    receipt = app.create(
        user_id="user-1",
        message="Teste re-enqueue",
        provider="openai",
        model_id="gpt-5.4",
        idempotency_key="multi-queued-1",
        code_mode="friend",
        live_mode=False,
    )
    # Insert a duplicate turn.queued event as happens when a turn is re-queued/recovered
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_events).values(
                conversation_id=receipt.conversation_id,
                message_id=receipt.message_id,
                user_id="user-1",
                event_type="turn.queued",
                payload={"state": "queued", "live_mode": False},
                created_at=datetime.now(UTC),
            )
        )

    chat_store._live_mode_turns.clear()
    claimed = chat_store.claim(receipt.turn_id)
    assert claimed is not None
    assert claimed["live_mode"] is False

    # Verify SpeakStreamParser preserves multi-space indentation in Markdown code blocks
    parser = SpeakStreamParser()
    code_md = "```python\ndef hello():\n    return 42\n```"
    r1 = parser.feed(code_md)
    r2 = parser.finish()
    assert r1.visual_delta + r2.visual_delta == code_md
    assert list(r1.chunks) + list(r2.chunks) == []

