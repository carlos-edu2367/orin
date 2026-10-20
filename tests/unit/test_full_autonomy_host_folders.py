"""Lote 0 (W6): ``full_autonomy`` opens the host file system only where the instance has host folders.

A shared Orin (SERVER profile) has no host folders: there the mode keeps every tool confined to the workspace and
no prompt promises access outside it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession, full_autonomy_workspace_hint
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.prompt import build_code_mode_system_prompt
from agentos.code_mode.system_prompt import render_code_turn_context

TURN = {
    "turn_id": "turn-1", "conversation_id": "chat_lote0", "user_id": "local-user", "execution_id": "exe-1",
    "provider": "openrouter", "model_id": "test-model", "user_message_id": "msg-user",
    "assistant_message_id": "msg-assistant", "permission_mode": "full_autonomy", "code_mode": "code",
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
    session = TurnSession(
        turn=dict(TURN), store=_Store(), agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path / "managed", enable_subagents=False, **kwargs,
    )
    session.workspace.root.mkdir(parents=True, exist_ok=True)
    return session


@pytest.fixture()
def host_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "host-folder"
    folder.mkdir()
    (folder / "secret.txt").write_text("HOSTDATA\n", encoding="utf-8")
    return folder


def _outside_calls(folder: Path) -> list[tuple[str, dict[str, str]]]:
    return [
        ("read_file", {"path": str(folder / "secret.txt")}),
        ("write_file", {"path": str(folder / "written.txt"), "content": "x"}),
        ("glob_files", {"pattern": "*.txt", "path": str(folder)}),
        ("search_files", {"pattern": "HOSTDATA", "path": str(folder)}),
    ]


def test_a_server_turn_in_full_autonomy_keeps_every_tool_inside_the_workspace(tmp_path: Path, host_folder: Path) -> None:
    session = _session(tmp_path, host_folders=False)
    tools = session._toolset(subagents=False, code_mode_active=True)
    assert session.workspace.unrestricted is False
    for name, args in _outside_calls(host_folder):
        outcome = tools.invoke(name, args)
        if os.name == "nt":
            assert outcome.status != "succeeded", (name, outcome.content)
        # POSIX reads the leading "/" inside the workspace (W1-R3): a call may succeed there, but nothing of the
        # host folder reaches the model.
        assert "HOSTDATA" not in (outcome.content or ""), (name, outcome.content)
        if outcome.status == "succeeded":  # a refusal may echo the requested path; a result never lists the host file
            assert "secret.txt" not in (outcome.content or ""), (name, outcome.content)
    assert not (host_folder / "written.txt").exists()
    if os.name != "nt":
        # POSIX (W1-R3): the confined workspace reads the leading "/" inside itself; the write lands there.
        landed = session.workspace.root.joinpath(*(host_folder / "written.txt").parts[1:])
        assert landed.is_file()


def test_a_desktop_turn_in_full_autonomy_still_reaches_the_host(tmp_path: Path, host_folder: Path) -> None:
    session = _session(tmp_path, host_folders=True)
    tools = session._toolset(subagents=False, code_mode_active=True)
    assert session.workspace.unrestricted is True
    read = tools.invoke("read_file", {"path": str(host_folder / "secret.txt")})
    assert read.status == "succeeded" and "HOSTDATA" in read.content


def test_a_toolset_built_without_host_folders_is_confined(tmp_path: Path, host_folder: Path) -> None:
    # Fail-closed default: only a caller that knows the instance has host folders opens them.
    workspace = ConversationWorkspace.at_root(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    tools = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy")
    assert workspace.unrestricted is False
    assert tools.invoke("read_file", {"path": str(host_folder / "secret.txt")}).status != "succeeded"


def test_leaving_plan_mode_on_a_server_does_not_open_the_host(tmp_path: Path, host_folder: Path) -> None:
    workspace = ConversationWorkspace.at_root(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    tools = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy", host_folders=False)
    tools.invoke("enter_plan_mode", {"reason": "x"})
    tools.invoke("exit_plan_mode", {"summary": "ready"})
    assert workspace.unrestricted is False


def test_no_prompt_promises_host_access_without_host_folders() -> None:
    assert full_autonomy_workspace_hint("full_autonomy", host_folders=False) == ""
    assert "outside the workspace" in full_autonomy_workspace_hint("full_autonomy", host_folders=True)
    assert full_autonomy_workspace_hint("accept_edits", host_folders=True) == ""

    server_prompt = build_code_mode_system_prompt(permission_mode="full_autonomy", host_access=False).lower()
    assert "outside the workspace" not in server_prompt
    assert "fora do workspace" not in server_prompt
    assert "qualquer caminho" not in server_prompt

    turn_ctx = render_code_turn_context(
        permission_mode="full_autonomy", plan_path=None, permits_push=False,
        environment={"os": "Linux"}, workspace_root="/srv/ws", workspace_is_local=False, git_status="",
        public_names=True, host_access=False,
    ).lower()
    assert "outside the workspace" not in turn_ctx
    assert "any path" not in turn_ctx
    assert "bypasspermissions" in turn_ctx
