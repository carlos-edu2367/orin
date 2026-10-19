"""W4 review fixes for the rewind routes: a blocked rewind is a 409 that names the blocking file (R10)."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.file_history import FileHistory
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
