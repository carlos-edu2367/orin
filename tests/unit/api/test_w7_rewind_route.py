"""W7 Batch F: the per-message rewind route — "both" (code, then a fork), and rewinding files too large to back up."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.code_mode import file_history as file_history_module
from agentos.code_mode.file_history import FileHistory

HEADERS = {"Authorization": "Bearer pat"}
URL = "/v1/conversations/conv-api/messages/um-1/rewind"


class _Conversations:
    def __init__(self) -> None:
        self.forks: list[dict[str, object]] = []

    def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
        return {"conversation_id": conversation_id, "title": "T", "state": "idle", "provider": "openai",
                "model_id": "m", "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

    def fork(self, conversation_id: str, user_id: str, *, before_message_id=None, title=None) -> dict[str, object]:
        self.forks.append({"conversation_id": conversation_id, "before_message_id": before_message_id})
        return {"conversation_id": "conv-fork", "title": "T (ramo)", "prompt": "rode os testes", "messages": 2, "project_id": None}


def _setup(tmp_path: Path) -> tuple[TestClient, _Conversations, Path, FileHistory]:
    conversations = _Conversations()
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(security=security, conversation_application=conversations,  # type: ignore[arg-type]
                           workspace_root=tmp_path / "workspaces", file_history_root=tmp_path / "history")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "conv-api")
    target = workspace.root / "app.py"
    target.write_bytes(b"original\n")
    history = FileHistory(tmp_path / "history", "conv-api")
    history.snapshot_before_write("um-1", target, turn_id="t1")
    target.write_bytes(b"modified\n")
    return TestClient(create_app(services)), conversations, target, history


def test_both_restores_the_code_and_then_forks_the_conversation(tmp_path: Path) -> None:
    client, conversations, target, _ = _setup(tmp_path)
    response = client.post(URL, json={"mode": "both"}, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "both" and body["restored"] == [str(target)] and body["fork"]["conversation_id"] == "conv-fork"
    assert target.read_bytes() == b"original\n"
    assert conversations.forks == [{"conversation_id": "conv-api", "before_message_id": "um-1"}]


def test_a_blocked_both_changes_nothing_and_does_not_fork(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, conversations, target, _ = _setup(tmp_path)
    monkeypatch.setattr(file_history_module, "MAX_BLOB_BYTES", 16)
    target.write_bytes(b"the person's later work, larger than the limit\n")
    response = client.post(URL, json={"mode": "both"}, headers=HEADERS)
    assert response.status_code == 409 and response.json()["error"]["code"] == "rewind_blocked"
    assert conversations.forks == [] and target.read_bytes().startswith(b"the person's later work")


def test_naming_the_blocked_file_rewinds_it_without_an_undo_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, target, _ = _setup(tmp_path)
    monkeypatch.setattr(file_history_module, "MAX_BLOB_BYTES", 16)
    target.write_bytes(b"the person's later work, larger than the limit\n")
    blocked = client.post(URL, json={"mode": "code"}, headers=HEADERS)
    named = [item["path"] for item in blocked.json()["error"]["details"]["files"]]
    assert named == [str(target)]
    response = client.post(URL, json={"mode": "code", "force_unbacked": named}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["unbacked"] == [str(target)] and target.read_bytes() == b"original\n"


def test_the_list_of_files_to_rewind_anyway_is_bounded(tmp_path: Path) -> None:
    client, _, _, _ = _setup(tmp_path)
    too_many = [f"C:/x/{index}.bin" for index in range(51)]
    assert client.post(URL, json={"mode": "code", "force_unbacked": too_many}, headers=HEADERS).status_code == 422
    assert client.post(URL, json={"mode": "code", "force_unbacked": [""]}, headers=HEADERS).status_code == 422
