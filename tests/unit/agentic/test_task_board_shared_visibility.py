"""Visibility, cross-conversation isolation, session wiring and API tests for WS-B3."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.task_board import TaskBoard
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.task_board import TaskBoardStore
from agentos.settings import EffectiveSettings, SettingsLayer


class _StubStore:
    def __init__(self, turn: dict[str, Any], engine: Any = None) -> None:
        self._turn = turn
        self._engine = engine

    def load(self, _turn_id: str) -> dict[str, Any]:
        return dict(self._turn)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Coordene as tarefas"}]

    def delta(self, _turn: object, text: str) -> None:
        pass

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def main_agent_id(self, turn: dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass


def _settings_with_task_board(enabled: bool) -> EffectiveSettings:
    data = {"orchestration": {"taskBoard": {"enabled": enabled}}}
    return EffectiveSettings(
        data=data,
        layers=(
            SettingsLayer(
                name="user",
                path=None,
                data=data,
            ),
        ),
        provenance={},
        errors=(),
        trusted=True,
    )


def _session(tmp_path: Path, *, conversation_id: str, enabled: bool, engine: Any = None) -> TurnSession:
    turn = {
        "turn_id": f"turn-{conversation_id}",
        "conversation_id": conversation_id,
        "user_id": "user-1",
        "execution_id": f"exe-{conversation_id}",
        "provider": "litellm",
        "model_id": "~anthropic/claude-opus-4-6",
        "user_message_id": "msg-user",
        "assistant_message_id": "msg-assistant",
        "code_mode": "code",
    }
    store = _StubStore(turn, engine=engine)
    session = TurnSession(
        turn=turn,
        store=store,
        provider_factory=lambda: object(),
        workspace_root=tmp_path / "workspaces",
        enable_terminal=False,
    )
    session.effective_settings = _settings_with_task_board(enabled)
    return session


def test_session_with_task_board_disabled_hides_task_claim_and_isolates_agents(tmp_path: Path) -> None:
    session = _session(tmp_path, conversation_id="conv-disabled", enabled=False)
    parent_ts = session._toolset(subagents=True, code_mode_active=True)
    child_ts = session._toolset(
        subagents=False,
        browser_agent_key="agent:child-off",
        code_mode_active=True,
        todos_state=[],
    )
    assert "task_claim" not in {d.name for d in parent_ts.definitions()}
    assert "task_claim" not in {d.name for d in child_ts.definitions()}

    parent_ts.execute("task_create", {"subject": "Só do pai", "description": "Local"})
    assert len(parent_ts.execute("task_list", {})["tasks"]) == 1
    assert child_ts.execute("task_list", {})["tasks"] == []


def test_parent_child_and_teammate_share_same_task_board_when_enabled(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    try:
        session = _session(tmp_path, conversation_id="conv-shared", enabled=True, engine=engine)

        parent_ts = session._toolset(subagents=True, code_mode_active=True)
        child_ts = session._toolset(
            subagents=False,
            browser_agent_key="agent:child-1",
            code_mode_active=True,
            todos_state=[],
        )
        teammate_ts = session._toolset(
            subagents=False,
            browser_agent_key="agent:mate-backend",
            code_mode_active=True,
            todos_state=[],
        )
        teammate_ts._team_sender = "backend"

        # All three see TaskClaim in their tool definitions.
        for ts in (parent_ts, child_ts, teammate_ts):
            assert "task_claim" in {d.name for d in ts.definitions()}

        # Parent creates two tasks on the shared board.
        t1 = parent_ts.execute("task_create", {"subject": "Criar endpoint", "description": "GET /items"})
        t2 = parent_ts.execute("task_create", {"subject": "Escrever testes", "description": "Unitários"})
        assert t1["ok"] and t2["ok"]
        id1, id2 = t1["task"]["id"], t2["task"]["id"]

        # Child and teammate both see the exact same tasks via TaskList & TaskGet.
        child_list = child_ts.execute("task_list", {})
        mate_list = teammate_ts.execute("task_list", {})
        assert {item["id"] for item in child_list["tasks"]} == {id1, id2}
        assert {item["id"] for item in mate_list["tasks"]} == {id1, id2}
        assert teammate_ts.execute("task_get", {"task_id": id1})["task"]["subject"] == "Criar endpoint"

        # Child claims task 1; teammate cannot claim task 1, claims task 2 instead.
        c1 = child_ts.execute("task_claim", {"task_id": id1})
        assert c1["ok"] is True
        assert c1["task"]["owner_agent_id"] == "agent:child-1"

        c1_conflict = teammate_ts.execute("task_claim", {"task_id": id1})
        assert c1_conflict["ok"] is False
        assert c1_conflict["error_code"] == "TASK_ALREADY_CLAIMED"

        c2 = teammate_ts.execute("task_claim", {"task_id": id2})
        assert c2["ok"] is True
        assert c2["task"]["owner_agent_id"] == "agent:mate-backend"

        # Child completes task 1; parent sees it completed via TaskGet.
        done = child_ts.execute("task_update", {"task_id": id1, "status": "completed"})
        assert done["ok"] is True
        seen_by_parent = parent_ts.execute("task_get", {"task_id": id1})
        assert seen_by_parent["task"]["status"] == "completed"
        assert seen_by_parent["task"]["owner_agent_id"] == "agent:child-1"

        # TodoWrite remains isolated per agent even when task board is enabled.
        child_ts.execute(
            "todo_write",
            {"todos": [{"content": "Checklist privado do filho", "status": "in_progress", "activeForm": "Fazendo"}]},
        )
        assert parent_ts._todos == []
        assert len(child_ts._todos) == 1
    finally:
        engine.dispose()


def test_cross_conversation_isolation_prevents_read_update_and_claim(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    try:
        board_a = TaskBoard(conversation_id="conv-A", engine=engine)
        board_b = TaskBoard(conversation_id="conv-B", engine=engine)

        ws_a = ConversationWorkspace(tmp_path, "conv-A")
        ws_b = ConversationWorkspace(tmp_path, "conv-B")
        ts_a = AgentToolset(ws_a, conversation_id="conv-A", task_board=board_a, task_board_enabled=True, task_board_agent_id="agent:a")
        ts_b = AgentToolset(ws_b, conversation_id="conv-B", task_board=board_b, task_board_enabled=True, task_board_agent_id="agent:b")

        created = ts_a.execute("task_create", {"subject": "Segredo da conversa A", "description": "Privado"})
        assert created["ok"] is True
        foreign_id = created["task"]["id"]

        # Conversation B cannot list, get, update, or claim conversation A's task.
        assert ts_b.execute("task_list", {})["tasks"] == []
        assert ts_b.execute("task_get", {"task_id": foreign_id})["ok"] is False
        assert ts_b.execute("task_update", {"task_id": foreign_id, "status": "completed"})["ok"] is False

        claim_foreign = ts_b.execute("task_claim", {"task_id": foreign_id})
        assert claim_foreign["ok"] is False
        assert claim_foreign["error_code"] == "TASK_NOT_FOUND"

        # Conversation A's task remains untouched.
        still_there = ts_a.execute("task_get", {"task_id": foreign_id})
        assert still_there["ok"] is True
        assert still_there["task"]["status"] == "pending"
        assert still_there["task"]["owner_agent_id"] is None
    finally:
        engine.dispose()


def test_tasks_api_preserves_tasks_and_exposes_conversation_board_items(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'api_board.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    upgrade(engine)
    try:
        store = TaskBoardStore(engine)
        item_c1 = store.create(conversation_id="c1", title="Tarefa C1", description="Do quadro de c1")
        store.create(conversation_id="c2", title="Tarefa C2", description="De outra conversa")

        class _ConversationsWithEngine:
            def __init__(self, eng: Any) -> None:
                self._engine = eng

            def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
                return {
                    "conversation_id": conversation_id,
                    "title": "T",
                    "state": "idle",
                    "provider": "openai",
                    "model_id": "m",
                    "messages": [],
                    "turns": [],
                    "activities": [],
                    "activity_cursor": "0",
                }

        security = InMemorySecurityService()
        security.add_pat(
            "test-pat",
            AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})),
        )
        services = ApiServices(
            security=security,
            conversation_application=_ConversationsWithEngine(engine),  # type: ignore[arg-type]
            workspace_root=tmp_path / "workspaces",
            capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL),
        )
        client = TestClient(create_app(services))
        resp = client.get("/v1/conversations/c1/tasks", headers={"Authorization": "Bearer test-pat"})
        assert resp.status_code == 200
        body = resp.json()
        assert "tasks" in body and isinstance(body["tasks"], list)
        assert "board_items" in body
        assert len(body["board_items"]) == 1
        assert body["board_items"][0]["item_id"] == item_c1.item_id
        assert body["board_items"][0]["title"] == "Tarefa C1"
        assert body["task_board"]["items"] == body["board_items"]
    finally:
        engine.dispose()
