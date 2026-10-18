from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.settings.paths import orin_user_dir


def test_a_non_interactive_code_toolset_refuses_an_ordinary_ask(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-ask")
    refusing = AgentToolset(workspace, code_mode_active=True, permission_mode="ask_permissions", non_interactive_ask="deny")
    outcome = refusing.invoke("run_command", {"command": "mkdir made_by_refused"})
    assert outcome.error_code == "CODE_PERMISSION_DENIED" and "nonInteractiveAsk" in outcome.content
    assert not (workspace.root / "made_by_refused").exists()
    allowing = AgentToolset(workspace, code_mode_active=True, permission_mode="ask_permissions", non_interactive_ask="allow")
    assert allowing.invoke("run_command", {"command": "mkdir made_by_allowed"}).status == "succeeded"


class Store:
    turn = {"turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am"}

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass
    def record(self, *args, **kwargs): pass
    def main_agent_id(self, turn): return "agent:c:main"


class Agents:
    def list(self): return []


def _allowed(tmp_path: Path, repository: dict | None = None, **turn) -> bool:
    store = Store()
    session = TurnSession(turn={**store.turn, **turn}, store=store, agents_store=Agents(), memory_store=None,
                          provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False)
    if repository is not None:
        path = session.workspace.root / ".orin" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(repository), encoding="utf-8")
    return session.build_runtime().toolset._non_interactive_ask_allowed


def test_the_session_decides_from_the_turn_and_the_user(tmp_path: Path) -> None:
    assert _allowed(tmp_path, code_mode="code") is True  # no explicit flag: unchanged
    assert _allowed(tmp_path, code_mode="code", interactive_permissions=False) is False
    assert _allowed(tmp_path, interactive_permissions=False) is True  # Chat mode is unchanged
    assert _allowed(tmp_path, {"permissions": {"nonInteractiveAsk": "allow"}}, code_mode="code", interactive_permissions=False) is False
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {"nonInteractiveAsk": "allow"}}), encoding="utf-8")
    assert _allowed(tmp_path, code_mode="code", interactive_permissions=False) is True
