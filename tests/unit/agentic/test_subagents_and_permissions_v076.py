from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

import sqlalchemy as sa

from agentos.agentic.agent_tools import AgentToolset, _BLOCKED_COMMAND
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.permissions import ToolPermissionBroker
from agentos.persistence.postgres.schema import (
    conversations,
    metadata,
    provider_configurations,
    provider_model_catalog,
    provider_model_favorites,
    users,
)
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker


NOW = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

TURN = {
    "turn_id": "turn-v076",
    "conversation_id": "conv-v076",
    "user_id": "user-1",
    "execution_id": "exe-v076",
    "provider": "litellm",
    "model_id": "~anthropic/claude-opus-4-6",
    "user_message_id": "msg-user",
    "assistant_message_id": "msg-assistant",
}


class RecordingStore:
    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.finished: list[tuple[bool, str | None]] = []
        self.activity: list[tuple[AgentActivityEventType, str, dict[str, Any], str | None]] = []
        self.tool_records: list[dict[str, object]] = []

    def load(self, turn_id: str) -> dict[str, Any]:
        return dict(TURN)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Execute a tarefa."}]

    def delta(self, _turn: object, text: str) -> None:
        self.deltas.append(text)

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))

    def record(
        self,
        _turn: object,
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        self.activity.append((event_type, summary, dict(payload or {}), agent_id))

    def main_agent_id(self, turn: Mapping[str, Any] | dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"

    def record_tool_call(
        self,
        _turn: object,
        *,
        tool_name: str,
        arguments: object,
        status: str,
        summary: str,
    ) -> None:
        self.tool_records.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "status": status,
                "summary": summary,
            }
        )

    def types(self) -> list[str]:
        return [item[0].value for item in self.activity]


class MemoryAgentsStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.states: list[tuple[str, str]] = []
        self.usage: dict[str, dict[str, int | str | None]] = {}

    def create(
        self,
        name: str,
        role: str,
        *,
        parent_agent_id: str,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        agent_id = f"agent:conv-v076:{name.lower()}"
        created = agent_id not in self.rows
        self.rows[agent_id] = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "parent_agent_id": parent_agent_id,
            "provider": provider,
            "model_id": model_id,
            "state": "idle",
        }
        return {**self.rows[agent_id], "created": created}

    def find(self, name: str) -> dict[str, Any] | None:
        return self.rows.get(f"agent:conv-v076:{name.lower()}")

    def set_state(self, agent_id: str, state: str) -> None:
        self.states.append((agent_id, state))

    def set_model(self, agent_id: str, model_id: str) -> None:
        if agent_id in self.rows:
            self.rows[agent_id]["model_id"] = model_id

    def list(self) -> list[dict[str, Any]]:
        return list(self.rows.values())

    def record_usage(
        self,
        agent_id: str,
        provider: str,
        model_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        self.usage[agent_id] = {
            "provider": provider,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }


class ScriptedProvider:
    def __init__(self, batches: list[list[NormalizedStreamItem] | Exception]) -> None:
        self.batches = list(batches)
        self.requests: list[dict[str, Any]] = []

    def stream(self, request: dict[str, Any]):
        self.requests.append(request)
        if not self.batches:
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="pronto"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return iter(batch)


def tool_call(call_id: str, name: str, arguments: dict[str, Any] | str) -> list[NormalizedStreamItem]:
    raw_args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return [
        NormalizedStreamItem(
            StreamKind.TOOL_CALL,
            1,
            tool_call_id=call_id,
            tool_name=name,
            arguments_delta=raw_args,
        ),
        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
    ]


def text(value: str) -> list[NormalizedStreamItem]:
    return [
        NormalizedStreamItem(StreamKind.TEXT, 1, text=value),
        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
    ]


def test_resolve_child_model_id_exact_fuzzy_slug_and_alias() -> None:
    from agentos.agentic.session import resolve_child_model_id

    current = "~anthropic/claude-opus-4-6"
    allowed = (
        "~anthropic/claude-opus-4-6",
        "~openai/gpt-luna-latest",
        "~anthropic/claude-haiku-4-5",
    )
    aliases = {"Luna Rápida": "~openai/gpt-luna-latest"}

    assert resolve_child_model_id(None, current, allowed) == current
    assert resolve_child_model_id("", current, allowed) == current
    assert resolve_child_model_id("~openai/gpt-luna-latest", current, allowed) == "~openai/gpt-luna-latest"
    assert resolve_child_model_id("openai/gpt-luna-latest", current, allowed) == "~openai/gpt-luna-latest"
    assert resolve_child_model_id("OPENAI/GPT-LUNA-LATEST", current, allowed) == "~openai/gpt-luna-latest"
    assert resolve_child_model_id("gpt-luna-latest", current, allowed) == "~openai/gpt-luna-latest"
    assert resolve_child_model_id("luna", current, allowed) == "~openai/gpt-luna-latest"
    assert resolve_child_model_id("haiku", current, allowed) == "~anthropic/claude-haiku-4-5"
    assert resolve_child_model_id("luna rápida", current, allowed, model_aliases=aliases) == "~openai/gpt-luna-latest"
    assert resolve_child_model_id("nonexistent-model", current, allowed) is None


def test_run_subagent_accepts_fuzzy_model_and_aliases_in_toolset(tmp_path: Path) -> None:
    store = RecordingStore()
    agents = MemoryAgentsStore()
    used_models: list[str] = []

    def child_provider_factory(model_id: str) -> ScriptedProvider:
        used_models.append(model_id)
        return ScriptedProvider([text("Relatório concluído pelo subagente com evidências completas.")])

    main_provider = ScriptedProvider(
        [
            tool_call(
                "c1",
                "run_subagent",
                {
                    "agent_type": "explore",
                    "role": "Auditoria rápida",
                    "model": "openai/gpt-luna-latest",
                    "task": "Verifique os arquivos do projeto",
                },
            ),
            tool_call(
                "c2",
                "run_subagent",
                {
                    "subagent_type": "luna",
                    "task": "Analise a estrutura",
                },
            ),
            text("Tudo concluído."),
        ]
    )

    session = TurnSession(
        turn={**TURN, "code_mode": "code"},
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: main_provider,
        workspace_root=tmp_path,
        child_model_ids=("~openai/gpt-luna-latest",),
        child_model_authorizer=lambda model_id: model_id in {"~openai/gpt-luna-latest"},
        child_provider_factory=child_provider_factory,
    )
    result = session.build_runtime().run(TURN["turn_id"])
    assert result.state == "completed"
    assert used_models == ["~openai/gpt-luna-latest", "~openai/gpt-luna-latest"]


def test_invalid_subagent_model_does_not_poison_db_and_subsequent_default_call_uses_current_model(
    tmp_path: Path,
) -> None:
    store = RecordingStore()
    agents = MemoryAgentsStore()
    used_models: list[str] = []

    def child_provider_factory(model_id: str) -> ScriptedProvider:
        used_models.append(model_id)
        return ScriptedProvider([text("Exploração concluída pelo subagente.")])

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: ScriptedProvider([text("Exploração concluída no modelo atual.")]),
        workspace_root=tmp_path,
        child_model_ids=("~openai/gpt-luna-latest",),
        child_model_authorizer=lambda model_id: model_id in {"~openai/gpt-luna-latest"},
        child_provider_factory=child_provider_factory,
    )

    # First call with invalid model_id must fail BEFORE writing to agents_store
    failed_outcome = session._run_subagent(
        subagent_type="explore",
        task="Tentar com modelo inválido",
        description="Teste",
        model_id="modelo-inexistente",
    )
    assert failed_outcome.status == "failed"
    assert failed_outcome.error_code == "CHILD_MODEL_NOT_FAVORITE"
    assert "~openai/gpt-luna-latest" in failed_outcome.content
    assert agents.find("explore") is None

    # Pre-seed a stale model in DB for explore to verify default call without model_id uses current_model_id
    agents.create(
        "explore",
        "Subagente read-only",
        parent_agent_id=session.main_agent_id,
        provider="litellm",
        model_id="stale-unfavorited-model",
    )

    ok_outcome = session._run_subagent(
        subagent_type="explore",
        task="Tentar sem model_id",
        description="Teste default",
        model_id=None,
    )
    assert ok_outcome.status == "succeeded"
    assert agents.find("explore")["model_id"] == "~anthropic/claude-opus-4-6"


def test_subagent_post_stream_failure_falls_back_to_parent_model_and_avoids_parent_turn_failed(
    tmp_path: Path,
) -> None:
    store = RecordingStore()
    agents = MemoryAgentsStore()
    used_models: list[str] = []

    def child_provider_factory(model_id: str) -> ScriptedProvider:
        used_models.append(model_id)
        if model_id == "~openai/gpt-luna-latest":
            return ScriptedProvider(
                [
                    RuntimeError("HTTP 404 from batch endpoint"),
                    RuntimeError("HTTP 404 from batch endpoint"),
                    RuntimeError("HTTP 404 from batch endpoint"),
                ]
            )
        return ScriptedProvider([text("Fallback no modelo pai funcionou perfeitamente.")])

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: ScriptedProvider([text("Fallback no modelo pai funcionou perfeitamente.")]),
        workspace_root=tmp_path,
        child_model_ids=("~openai/gpt-luna-latest",),
        child_model_authorizer=lambda model_id: model_id in {"~openai/gpt-luna-latest"},
        child_provider_factory=child_provider_factory,
    )

    outcome = session._run_subagent(
        subagent_type="explore",
        task="Auditar repositório",
        description="Auditoria",
        model_id="luna",
    )
    assert outcome.status == "succeeded"
    assert "Fallback no modelo pai funcionou perfeitamente." in outcome.content
    assert "~openai/gpt-luna-latest" in used_models
    assert "turn.failed" not in store.types()
    assert "model.routing_started" in store.types()


def test_subagent_complete_failure_emits_delegation_failed_without_parent_turn_failed(
    tmp_path: Path,
) -> None:
    store = RecordingStore()
    agents = MemoryAgentsStore()

    failing_provider = ScriptedProvider(
        [
            RuntimeError("transient error 1"),
            RuntimeError("transient error 2"),
            RuntimeError("transient error 3"),
        ]
    )

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: failing_provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _model_id: failing_provider,
    )

    outcome = session._run_subagent(
        subagent_type="explore",
        task="Falhar todas as tentativas",
        description="Teste falha",
    )
    assert outcome.status == "failed"
    assert "delegation.failed" in store.types()
    assert "turn.failed" not in store.types()


def test_chat_worker_favorite_models_exclude_batch_and_support_enabled_cross_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            provider_configurations.insert(),
            [
                {
                    "user_id": "user-1",
                    "provider": "litellm",
                    "enabled": True,
                    "model": "~anthropic/claude-opus-4-6",
                    "base_url": "https://litellm.local",
                    "secret_ref": "sec-1",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "anthropic",
                    "enabled": True,
                    "model": "claude-haiku-4-5",
                    "base_url": "https://anthropic.local",
                    "secret_ref": "sec-2",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "ollama",
                    "enabled": False,
                    "model": "qwen3",
                    "base_url": "http://localhost:11434",
                    "secret_ref": "sec-3",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        conn.execute(
            provider_model_catalog.insert(),
            [
                {
                    "user_id": "user-1",
                    "provider": "litellm",
                    "model_id": "~openai/gpt-luna-latest",
                    "display_name": "GPT Luna",
                    "context_window": 128000,
                    "capabilities": ["Tools"],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "route_kind": "model",
                    "refreshed_at": NOW,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "litellm",
                    "model_id": "~openai/gpt-5-nano:batch",
                    "display_name": "GPT Nano Batch",
                    "context_window": 128000,
                    "capabilities": ["Tools"],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "route_kind": "model",
                    "refreshed_at": NOW,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "anthropic",
                    "model_id": "claude-haiku-4-5",
                    "display_name": "Claude Haiku",
                    "context_window": 200000,
                    "capabilities": ["Tools"],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "route_kind": "model",
                    "refreshed_at": NOW,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "ollama",
                    "model_id": "disabled-ollama-model",
                    "display_name": "Disabled Ollama",
                    "context_window": 32000,
                    "capabilities": ["Tools"],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "route_kind": "model",
                    "refreshed_at": NOW,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        conn.execute(
            provider_model_favorites.insert(),
            [
                {
                    "user_id": "user-1",
                    "provider": "litellm",
                    "model_id": "~openai/gpt-luna-latest",
                    "created_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "litellm",
                    "model_id": "~openai/gpt-5-nano:batch",
                    "created_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "anthropic",
                    "model_id": "claude-haiku-4-5",
                    "created_at": NOW,
                },
                {
                    "user_id": "user-1",
                    "provider": "ollama",
                    "model_id": "disabled-ollama-model",
                    "created_at": NOW,
                },
            ],
        )

    class CatalogStore:
        _engine = engine

    worker = ChatWorker(CatalogStore())
    turn = {"user_id": "user-1", "provider": "litellm", "model_id": "~anthropic/claude-opus-4-6"}
    favorites = worker._favorite_child_model_ids(turn)

    assert "~openai/gpt-luna-latest" in favorites
    assert "claude-haiku-4-5" in favorites
    assert "~openai/gpt-5-nano:batch" not in favorites
    assert "disabled-ollama-model" not in favorites

    assert worker._is_favorite_child_model(turn, "openai/gpt-luna-latest") is True
    assert worker._is_favorite_child_model(turn, "claude-haiku-4-5") is True
    assert worker._is_favorite_child_model(turn, "~openai/gpt-5-nano:batch") is False
    assert worker._is_favorite_child_model(turn, "disabled-ollama-model") is False


def test_emit_lifecycle_maps_thinking_delta_and_generating_tool_call(tmp_path: Path) -> None:
    store = RecordingStore()
    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=MemoryAgentsStore(),
        memory_store=None,
        provider_factory=lambda: ScriptedProvider([text("ok")]),
        workspace_root=tmp_path,
    )

    session.emit_lifecycle("thinking_delta", {"delta": "Analisando dependências...", "iteration": 2})
    session.emit_lifecycle(
        "generating_tool_call",
        {
            "tool_name": "write_file",
            "call_id": "call-99",
            "bytes_received": 512,
        },
    )

    assert len(store.activity) == 2
    thought_type, thought_summary, thought_payload, _ = store.activity[0]
    expected_thought_type = getattr(
        AgentActivityEventType,
        "AGENT_THOUGHT_DELTA",
        AgentActivityEventType.CONTEXT_UPDATED,
    )
    assert thought_type == expected_thought_type
    assert thought_summary == "Pensando"
    assert thought_payload.get("delta") == "Analisando dependências..."
    assert thought_payload.get("iteration") == 2

    gen_type, gen_summary, gen_payload, _ = store.activity[1]
    assert gen_type == AgentActivityEventType.TOOL_PROGRESSED
    assert gen_summary == "Preparando write_file..."
    assert gen_payload.get("phase") == "generating_arguments"
    assert gen_payload.get("tool_name") == "write_file"
    assert gen_payload.get("call_id") == "call-99"
    assert gen_payload.get("bytes_received") == 512


def test_interactive_permissions_enabled_and_permission_broker_emits_both_rule_keys(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)

    class _MinimalStore:
        _engine = engine

    worker = ChatWorker(_MinimalStore())
    hydrated_turn = worker._hydrate_code_mode({**TURN, "code_mode": "code"})
    assert hydrated_turn["interactive_permissions"] is True

    store = RecordingStore()
    session = TurnSession(
        turn=hydrated_turn,
        store=store,
        agents_store=MemoryAgentsStore(),
        memory_store=None,
        provider_factory=lambda: ScriptedProvider([text("ok")]),
        workspace_root=tmp_path,
    )
    runtime = session.build_runtime()
    assert runtime.toolset._interactive_permissions is True

    emitted_events: list[tuple[str, str, dict[str, Any]]] = []
    broker = ToolPermissionBroker(
        tmp_path,
        event_emitter=lambda ev_type, summary, payload: emitted_events.append((ev_type, summary, dict(payload))),
    )

    def _approve_shortly() -> None:
        while not emitted_events:
            pass
        req_id = str(emitted_events[0][2]["permission_request_id"])
        broker.resolve_request(req_id, decision="allow_once")

    approver = threading.Thread(target=_approve_shortly, daemon=True)
    approver.start()
    result = broker.request_and_wait(
        tool_name="run_command",
        arguments={"command": "git status"},
        timeout_seconds=2.0,
    )
    approver.join(timeout=2.0)

    assert result["decision"] == "allow_once"
    assert len(emitted_events) == 1
    ev_type, _summary, payload = emitted_events[0]
    assert ev_type == "code_mode.decision_required"
    assert payload.get("suggested_rule") == "run_command(git status:*)"
    assert payload.get("rule_suggestion") == "run_command(git status:*)"
    nested = payload.get("permission_request")
    assert isinstance(nested, dict)
    assert nested.get("suggested_rule") == "run_command(git status:*)"
    assert nested.get("rule_suggestion") == "run_command(git status:*)"


def test_blocked_command_allows_format_table_and_flags_but_blocks_disk_format() -> None:
    assert _BLOCKED_COMMAND.search("Get-Process | Select-Object Id | Format-Table -AutoSize") is None
    assert _BLOCKED_COMMAND.search("ngrok http 3000 --log-format json") is None
    assert _BLOCKED_COMMAND.search("npm run format") is None
    assert _BLOCKED_COMMAND.search(" ruff format src/ ") is None

    assert _BLOCKED_COMMAND.search("format C: /q") is not None
    assert _BLOCKED_COMMAND.search("format.com D:") is not None
    assert _BLOCKED_COMMAND.search("diskpart") is not None


def test_verification_command_with_screenshots_preserves_code_mode_verified(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-v076")
    (workspace.root / "src").mkdir(parents=True, exist_ok=True)
    (workspace.root / "src" / "App.tsx").write_text("export const App = () => <div>v1</div>;\n", encoding="utf-8")

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        code_mode_work_kind="implementation",
        permission_mode="bypass_permissions",
    )

    toolset.invoke("read_file", {"path": "src/App.tsx"})
    toolset.invoke(
        "edit_file",
        {
            "path": "src/App.tsx",
            "old_text": "<div>v1</div>",
            "new_text": "<div>v2</div>",
        },
    )
    assert toolset._code_mode_verified is False

    # Simulate a verification command that also writes a screenshot inside shots/
    (workspace.root / "scripts").mkdir(parents=True, exist_ok=True)
    (workspace.root / "scripts" / "audit-ui.mjs").write_text(
        "import fs from 'node:fs';\n"
        "fs.mkdirSync('shots', { recursive: true });\n"
        "fs.writeFileSync('shots/home.png', 'png-bytes');\n"
        "console.log('AUDIT OK');\n",
        encoding="utf-8",
    )

    outcome = toolset.invoke("run_command", {"command": "node scripts/audit-ui.mjs"})
    assert outcome.status == "succeeded"
    assert toolset._code_mode_verified is True


def test_edit_file_tolerates_empty_old_text_with_edits_list_and_empty_edits_with_old_text(
    tmp_path: Path,
) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-v076")
    (workspace.root / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    toolset = AgentToolset(workspace, permission_mode="bypass_permissions")

    # Case 1: old_text="" and new_text="" alongside non-empty edits=[...]
    out1 = toolset.invoke(
        "edit_file",
        {
            "path": "app.py",
            "old_text": "",
            "new_text": "",
            "edits": [{"old_text": "a = 1", "new_text": "a = 10"}],
        },
    )
    assert out1.status == "succeeded"
    assert "a = 10" in (workspace.root / "app.py").read_text(encoding="utf-8")

    # Case 2: old_text and new_text provided alongside empty edits=[]
    out2 = toolset.invoke(
        "edit_file",
        {
            "path": "app.py",
            "old_text": "b = 2",
            "new_text": "b = 20",
            "edits": [],
        },
    )
    assert out2.status == "succeeded"
    assert "b = 20" in (workspace.root / "app.py").read_text(encoding="utf-8")


def test_session_activity_labels_edit_file_and_non_validation_run_command(tmp_path: Path) -> None:
    store = RecordingStore()
    provider = ScriptedProvider(
        [
            tool_call("c0", "write_file", {"path": "hello.py", "content": "x = 1\n"}),
            tool_call("c1", "read_file", {"path": "hello.py"}),
            tool_call(
                "c2",
                "edit_file",
                {"path": "hello.py", "old_text": "x = 1", "new_text": "x = 2"},
            ),
            tool_call("c3", "run_command", {"command": "python -c \"print('hello')\""}),
            text("Concluído."),
        ]
    )
    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=MemoryAgentsStore(),
        memory_store=None,
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
    )
    session.build_runtime().run(TURN["turn_id"])

    artifact_summaries = [
        summary
        for evt_type, summary, _payload, _agent in store.activity
        if evt_type == AgentActivityEventType.ARTIFACT_CREATED
    ]
    assert "Editou hello.py" in artifact_summaries

    assert "code_mode.validation_started" not in store.types()
    assert "code_mode.validation_finished" not in store.types()
