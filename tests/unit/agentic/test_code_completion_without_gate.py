from __future__ import annotations

import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, insert, select

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import code_mode_runs, metadata
from agentos.workers.chat import ChatWorker


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "provider": "openrouter",
        "model_id": "local-model",
        "user_message_id": "user-message-1",
        "assistant_message_id": "assistant-message-1",
    }


class _Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []

    def load(self, turn_id: str):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "reformule o orçamento"}]

    def lifecycle(self, turn, state, **payload) -> None:
        self.events.append((state, payload))

    def delta(self, turn, text) -> None:
        self.text.append(text)

    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None:
        ...


def _json_args(payload: dict) -> str:
    return json.dumps(json.dumps(payload))[1:-1]


def _tool_call(call_id: str, name: str, arguments: str) -> str:
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
        % (call_id, name, arguments)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


def _code_runtime(
    tmp_path: Path, provider: _ScriptedProvider
) -> tuple[_Store, AgenticTurnRuntime]:
    store = _Store()
    store.turn["code_mode"] = "code"
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(
            ConversationWorkspace(tmp_path, "c1"), code_mode_active=True
        ),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=None,
    )
    return store, runtime


def test_answer_after_an_edit_without_checks_completes_unverified(tmp_path: Path):
    provider = _ScriptedProvider(
        [
            _tool_call(
                "c1",
                "write_file",
                _json_args({"path": "a.py", "content": "x = 1\n"}),
            ),
            _ANSWER,
        ]
    )
    store, runtime = _code_runtime(tmp_path, provider)
    result = runtime.run("turn-1")
    assert result.state == "completed" and result.verification == "unverified"
    assert ("code_verification", {"status": "unverified"}) in store.events
    assert len(provider.requests) == 2  # nenhuma rodada extra pedindo verify_project
    assert not any(
        "Sistema do Modo Code" in str(r["messages"]) for r in provider.requests
    )


def test_answer_without_tools_is_accepted(tmp_path: Path):
    store, runtime = _code_runtime(tmp_path, _ScriptedProvider([_ANSWER]))
    result = runtime.run("turn-1")
    assert result.state == "completed" and result.verification == "not_applicable"


def test_verification_status_reflects_a_passing_check(tmp_path: Path):
    tools = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True)
    tools.invoke("write_file", {"path": "a.py", "content": "x = 1\n"})
    assert tools.code_verification_status() == "unverified"
    tools.invoke("run_command", {"command": f'"{sys.executable}" -m pytest --version'})
    assert tools.code_verification_status() == "verified"


def test_settle_code_mode_sets_completion_kind_unverified(tmp_path: Path):
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1",
        message="Altere a.py",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="code-unverified-settle",
        code_mode="code",
    )
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None
    worker = ChatWorker(store, workspace_root=str(tmp_path))
    hydrated = worker._hydrate_code_mode(claimed)

    worker._settle_code_mode(
        hydrated, "completed", error_code=None, verification="unverified"
    )
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(code_mode_runs).where(
                    code_mode_runs.c.execution_id == claimed["execution_id"]
                )
            )
            .mappings()
            .one()
        )
    assert row["stage"] == "completed"
    assert row["completion_kind"] == "unverified"
    assert AgentActivityEventType.CODE_MODE_UNVERIFIED == "code_mode.unverified"


def test_turn_session_records_code_mode_unverified_event(tmp_path: Path):
    from agentos.agentic.session import TurnSession

    recorded: list[tuple[AgentActivityEventType, str, dict[str, object]]] = []

    class _DummySessionStore:
        def record(self, turn, event_type, summary, payload, **_kw):
            recorded.append((event_type, summary, dict(payload or {})))

    class _DummyAgents:
        def list(self):
            return []

    turn = {
        "turn_id": "turn-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "execution_id": "exec-1",
        "code_mode": "code",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
    }
    session = TurnSession(
        turn=turn,
        store=_DummySessionStore(),
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=str(tmp_path),
    )
    session.toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "conv-1"), code_mode_active=True
    )
    session.toolset._changed_paths.update({"a.py", "b.py"})
    session.emit_lifecycle(turn, "code_verification", status="unverified")
    session.emit_lifecycle(turn, "code_verification", status="verified")
    unverified_events = [
        item for item in recorded if item[0] is AgentActivityEventType.CODE_MODE_UNVERIFIED
    ]
    assert unverified_events == [
        (
            AgentActivityEventType.CODE_MODE_UNVERIFIED,
            "Entrega não verificada",
            {"verification": "unverified", "changed_paths": 2},
        )
    ]


