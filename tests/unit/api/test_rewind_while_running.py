"""W4: a rewind never races a running turn; a message rewind restores in folders added with /add-dir."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.code_mode.file_history import FileHistory
from agentos.conversations.chat import PostgresChatStore
from agentos.conversations.directories import ConversationDirectoryStore
from agentos.persistence.postgres.schema import metadata

HEADERS = {"Authorization": "Bearer pat"}


def _client(tmp_path: Path, *, state: str = "idle", turn_state: str = "completed", directories=None) -> TestClient:
    class _Conversations:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            return {"conversation_id": conversation_id, "title": "T", "state": state, "provider": "openai", "model_id": "m",
                    "messages": [], "turns": [{"turn_id": "t1", "state": turn_state}], "activities": [], "activity_cursor": "0"}

    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=_Conversations(),  # type: ignore[arg-type]
        workspace_root=tmp_path / "workspaces", file_history_root=tmp_path / "history", conversation_directories=directories,
    )
    return TestClient(create_app(services))


@pytest.mark.parametrize(("state", "turn_state"), [("running", "running"), ("idle", "queued"), ("queued", "completed"), ("idle", "cancelling")])
def test_both_rewinds_refuse_while_a_turn_runs(tmp_path: Path, state: str, turn_state: str) -> None:
    client = _client(tmp_path, state=state, turn_state=turn_state)
    workspace = ConversationWorkspace(tmp_path / "workspaces", "conv-r")
    target = workspace.root / "app.py"
    target.write_bytes(b"original\n")
    FileHistory(tmp_path / "history", "conv-r").snapshot_before_write("um-1", target, turn_id="t1")
    target.write_bytes(b"modified\n")
    message = client.post("/v1/conversations/conv-r/messages/um-1/rewind", json={"mode": "code"}, headers=HEADERS)
    turn = client.post("/v1/conversations/conv-r/turns/t1/rewind", headers=HEADERS)
    assert message.status_code == 409 and message.json()["error"]["code"] == "turn_running"
    assert turn.status_code == 409
    assert target.read_bytes() == b"modified\n"  # nothing was restored


def test_a_message_rewind_restores_in_an_added_folder(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'orin.db').as_posix()}", future=True)
    metadata.create_all(engine)
    conversation_id = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="m", idempotency_key="c1").conversation_id
    other = tmp_path / "other"
    other.mkdir()
    store = ConversationDirectoryStore(engine)
    store.add("user-1", conversation_id, str(other), workspace_root=tmp_path / "workspaces")
    target = other / "lib.py"
    target.write_bytes(b"original\n")
    FileHistory(tmp_path / "history", conversation_id).snapshot_before_write("um-1", target.resolve(), turn_id="t1")
    target.write_bytes(b"modified\n")
    response = _client(tmp_path, directories=store).post(f"/v1/conversations/{conversation_id}/messages/um-1/rewind", json={"mode": "code"}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert target.read_bytes() == b"original\n"
