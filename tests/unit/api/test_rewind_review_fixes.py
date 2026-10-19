"""W4 review fixes for the rewind routes: a blocked rewind is a 409 that names the blocking file (R10), and a rewind
never overlaps a turn of the same folder, including a sibling chat's (R11)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.workspace import ConversationWorkspace, resolve_workspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.code_mode.file_history import FileHistory
from agentos.code_mode.workspace_lock import enter_turn, exit_turn
from tests.unit.api.test_rewind_while_running import HEADERS, _client


def test_a_blocked_message_rewind_is_a_409_that_names_the_file_and_changes_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path, state="idle", turn_state="completed")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "conv-b")
    target = workspace.root / "app.py"
    target.write_bytes(b"original\n")
    history = FileHistory(tmp_path / "history", "conv-b")
    entry = history.snapshot_before_write("um-1", target, turn_id="t1")
    target.write_bytes(b"modified\n")
    (history.base / "blobs" / entry["sha256"]).write_bytes(b"tampered")  # the backup no longer matches

    response = client.post("/v1/conversations/conv-b/messages/um-1/rewind", json={"mode": "code"}, headers=HEADERS)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "rewind_blocked" and error["retryable"] is False
    assert error["details"]["files"] == [{"path": str(target), "reason": "its backup does not match its checksum"}]
    assert target.read_bytes() == b"modified\n"
    assert [item["message_id"] for item in history.messages()] == ["um-1"]  # no undo snapshot was kept


def test_a_rewind_that_applies_reports_it_is_not_blocked(tmp_path: Path) -> None:
    client = _client(tmp_path, state="idle", turn_state="completed")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "conv-ok")
    target = workspace.root / "app.py"
    target.write_bytes(b"original\n")
    FileHistory(tmp_path / "history", "conv-ok").snapshot_before_write("um-2", target, turn_id="t1")
    target.write_bytes(b"modified\n")

    response = client.post("/v1/conversations/conv-ok/messages/um-2/rewind", json={"mode": "code"}, headers=HEADERS)

    assert response.status_code == 200 and response.json()["blocked"] is False
    assert target.read_bytes() == b"original\n"


def _project_client(tmp_path: Path) -> TestClient:
    """Two chats of one project: both resolve to the project's folder."""

    class _Conversations:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            return {"conversation_id": conversation_id, "project_id": "p1", "title": "T", "state": "idle", "provider": "openai",
                    "model_id": "m", "messages": [], "turns": [{"turn_id": "t", "state": "completed"}], "activities": [], "activity_cursor": "0"}

    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=_Conversations(),  # type: ignore[arg-type]
        workspace_root=tmp_path / "workspaces", file_history_root=tmp_path / "history",
    )
    return TestClient(create_app(services))


def test_a_sibling_chats_running_turn_refuses_a_rewind_in_this_chat(tmp_path: Path) -> None:
    client = _project_client(tmp_path)
    shared = resolve_workspace("workspace:p1", managed_root=tmp_path / "workspaces", local_root=None)
    service_py = shared.root / "service.py"
    _write(service_py, b"v0\n")
    FileHistory(tmp_path / "history", "chat_a").snapshot_before_write("msg_a", service_py, turn_id="ta")
    _write(service_py, b"v1 (chat A)\n")
    running = enter_turn(shared.root)  # chat B's turn runs in the same folder
    try:
        message = client.post("/v1/conversations/chat_a/messages/msg_a/rewind", json={"mode": "code"}, headers=HEADERS)
        turn = client.post("/v1/conversations/chat_a/turns/ta/rewind", headers=HEADERS)
    finally:
        exit_turn(running)

    assert message.status_code == 409 and message.json()["error"]["code"] == "turn_running"
    assert turn.status_code == 409 and turn.json()["error"]["code"] == "turn_running"
    assert service_py.read_bytes() == b"v1 (chat A)\n"

    response = client.post("/v1/conversations/chat_a/messages/msg_a/rewind", json={"mode": "code"}, headers=HEADERS)
    assert response.status_code == 200 and service_py.read_bytes() == b"v0\n"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
