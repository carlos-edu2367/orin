"""W4: the toolset records edits in the file history; the session and the API wire it."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.code_mode.file_history import FileHistory


def test_edits_are_recorded_per_user_message_and_can_be_rewound(tmp_path: Path) -> None:
    history = FileHistory(tmp_path / "history", "c1")
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path / "w", "c1"), file_history=history, user_message_id="msg-1", turn_id="turn-1",
    )
    (toolset.workspace.root / "kept.txt").write_bytes(b"keep me\n")
    toolset.invoke("Write", {"file_path": "a.txt", "content": "one\n"})
    toolset.invoke("Read", {"file_path": "a.txt"})
    toolset.invoke("Edit", {"file_path": "a.txt", "old_string": "one", "new_string": "two"})
    toolset.invoke("Read", {"file_path": "kept.txt"})
    toolset.invoke("Write", {"file_path": "kept.txt", "content": "changed\n"})
    [message] = history.messages()
    assert message["message_id"] == "msg-1" and message["turn_id"] == "turn-1" and len(message["files"]) == 2
    result = history.rewind("msg-1", roots=[toolset.workspace.root])
    assert not (toolset.workspace.root / "a.txt").exists()
    assert (toolset.workspace.root / "kept.txt").read_bytes() == b"keep me\n"
    assert result["skipped"] == []


def test_without_a_history_edits_still_work(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"))
    assert toolset.invoke("Write", {"file_path": "a.txt", "content": "x"}).status == "succeeded"


class _Store:
    def main_agent_id(self, turn):
        return f"agent:{turn['conversation_id']}:main"

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def record(self, *args, **kwargs):
        return None


class _Agents:
    def list(self):
        return []


def test_the_session_gives_every_toolset_the_history(tmp_path: Path) -> None:
    session = TurnSession(
        turn={
            "turn_id": "turn-1", "conversation_id": "conv-h", "user_id": "local-user", "execution_id": "exe-1",
            "user_message_id": "um-1", "code_mode": "code", "provider": "openrouter", "model_id": "m",
        },
        store=_Store(), agents_store=_Agents(), memory_store=None, provider_factory=lambda: None,
        workspace_root=tmp_path / "w", cancelled=lambda _turn: False, file_history_root=tmp_path / "history",
    )
    toolset = session.build_runtime().toolset
    assert isinstance(toolset._file_history, FileHistory)
    assert toolset._history_message_id == "um-1"


def _client(tmp_path: Path) -> TestClient:
    class _Conversations:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            return {"conversation_id": conversation_id, "title": "T", "state": "idle", "provider": "openai",
                    "model_id": "m", "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=_Conversations(),  # type: ignore[arg-type]
        workspace_root=tmp_path / "workspaces", file_history_root=tmp_path / "history",
    )
    return TestClient(create_app(services))


def test_the_api_rewinds_one_message_and_lists_the_history(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer pat"}
    workspace = ConversationWorkspace(tmp_path / "workspaces", "conv-api")
    target = workspace.root / "app.py"
    target.write_bytes(b"original\n")
    history = FileHistory(tmp_path / "history", "conv-api")
    history.snapshot_before_write("um-1", target, turn_id="t1")
    target.write_bytes(b"modified\n")
    listed = client.get("/v1/conversations/conv-api/file-history", headers=headers)
    assert listed.status_code == 200 and listed.json()["items"][0]["message_id"] == "um-1"
    response = client.post("/v1/conversations/conv-api/messages/um-1/rewind", json={"mode": "code"}, headers=headers)
    assert response.status_code == 200 and response.json()["restored"] == [str(target)]
    assert target.read_bytes() == b"original\n"
    assert client.post("/v1/conversations/conv-api/messages/nope/rewind", json={"mode": "code"}, headers=headers).status_code == 404
    # W7: "conversation" and "both" exist now (they fork the conversation); an unknown mode is still refused.
    assert client.post("/v1/conversations/conv-api/messages/um-1/rewind", json={"mode": "everything"}, headers=headers).status_code == 422
