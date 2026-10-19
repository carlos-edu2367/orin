"""W4 review fixes: the turn's own working directories (W4-R1, W4-R19)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir

TURN = {
    "turn_id": "turn-1", "conversation_id": "chat_review", "user_id": "local-user", "execution_id": "exe-1",
    "provider": "openrouter", "model_id": "test-model", "user_message_id": "msg-user", "assistant_message_id": "msg-assistant",
}


class _Store:
    def load(self, turn_id: str) -> dict:
        return TURN

    def history_for_turn(self, turn) -> list[dict[str, object]]:
        return []

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        pass


class _Agents:
    def list(self) -> list[dict]:
        return []


def _session(tmp_path: Path, **kwargs) -> TurnSession:
    return TurnSession(
        turn=dict(TURN), store=_Store(), agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path / "managed", enable_subagents=False, **kwargs,
    )


@pytest.fixture()
def repo_listing_host_folder(tmp_path: Path):
    """A workspace whose trusted .claude/settings.json lists a real folder outside it."""
    session = _session(tmp_path)
    workspace = session.workspace.root
    workspace.mkdir(parents=True, exist_ok=True)
    host = tmp_path / "host-folder"
    host.mkdir()
    (host / "secret.txt").write_text("host data\n", encoding="utf-8")
    (workspace / ".claude").mkdir()
    (workspace / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"additionalDirectories": [str(host)]}}), encoding="utf-8",
    )
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    return workspace, host


def test_a_server_without_host_folders_applies_no_settings_directories(repo_listing_host_folder, tmp_path: Path, monkeypatch) -> None:
    workspace, host = repo_listing_host_folder
    # The store must not even be consulted: a closed host_folders capability reads neither source.
    monkeypatch.setattr(TurnSession, "_directory_store", lambda self: pytest.fail("the directory store was read"))
    session = _session(tmp_path, host_folders=False)
    session.effective_settings = load_effective_settings(workspace, trusted=True)
    session._apply_working_directories()
    assert session.workspace.additional_roots == ()
    assert not [item for item in session._reminders.drain() if "working directories" in item.text]
    toolset = AgentToolset(session.workspace, public_tool_names=True)
    read = toolset.invoke("Read", {"file_path": str((host / "secret.txt").resolve())})
    assert read.status != "succeeded"


def test_a_desktop_turn_applies_the_trusted_settings_directories(repo_listing_host_folder, tmp_path: Path) -> None:
    workspace, host = repo_listing_host_folder
    session = _session(tmp_path)
    session.effective_settings = load_effective_settings(workspace, trusted=True)
    session._apply_working_directories()
    assert session.workspace.additional_roots == (host.resolve(),)
