"""W4: /add-dir — the conversation's stored folders, the API and the DirectoryAdded hook."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.agentic.directories import DirectoryError
from agentos.conversations.chat import PostgresChatStore
from agentos.conversations.directories import ConversationDirectoryStore
from agentos.persistence.postgres.schema import metadata


@pytest.fixture()
def store_and_conversation(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'orin.db').as_posix()}", future=True)
    metadata.create_all(engine)
    receipt = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="m", idempotency_key="c1", code_mode="code")
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    return ConversationDirectoryStore(engine), receipt.conversation_id, workspace, other


def test_add_list_announce_and_remove(store_and_conversation) -> None:
    store, conversation_id, workspace, other = store_and_conversation
    directory, created = store.add("user-1", conversation_id, str(other), workspace_root=workspace)
    assert created and directory["path"] == str(other.resolve()) and directory["directory_id"].startswith("dir_")
    again, created_again = store.add("user-1", conversation_id, str(other) + "/", workspace_root=workspace)
    assert not created_again and again["directory_id"] == directory["directory_id"]
    assert [item["path"] for item in store.list("user-1", conversation_id)] == [str(other.resolve())]
    assert store.take_unannounced("user-1", conversation_id) == [str(other.resolve())]
    assert store.take_unannounced("user-1", conversation_id) == []  # announced once
    assert store.remove("user-1", conversation_id, directory["directory_id"]) is True
    assert store.remove("user-1", conversation_id, directory["directory_id"]) is False
    assert store.list("user-1", conversation_id) == []


def test_another_persons_conversation_is_not_found(store_and_conversation) -> None:
    store, conversation_id, workspace, other = store_and_conversation
    with pytest.raises(LookupError):
        store.add("user-2", conversation_id, str(other), workspace_root=workspace)
    with pytest.raises(LookupError):
        store.list("user-2", conversation_id)
    assert store.paths("user-2", conversation_id) == []


def test_unsafe_folders_are_never_stored(store_and_conversation) -> None:
    store, conversation_id, workspace, _ = store_and_conversation
    for raw in (str(Path.home()), str(workspace), "\\\\server\\share", str(Path(workspace.anchor))):
        with pytest.raises(DirectoryError):
            store.add("user-1", conversation_id, raw, workspace_root=workspace)
    assert store.list("user-1", conversation_id) == []


def test_a_turn_uses_the_folders_and_fires_directory_added_once(store_and_conversation, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.agentic.lifecycle import HookEvent, LifecycleBus
    from agentos.agentic.reminders import ReminderQueue
    from agentos.agentic.session import TurnSession

    store, conversation_id, workspace, other = store_and_conversation
    store.add("user-1", conversation_id, str(other), workspace_root=workspace)
    events = []
    bus = LifecycleBus()
    bus.subscribe(events.append)
    session = TurnSession.__new__(TurnSession)
    session.turn = {"user_id": "user-1", "conversation_id": conversation_id}
    session.host_folders = True  # set by __init__ in production; this test builds the object without it (W4-R1)
    session.chat_store = type("Store", (), {"_engine": store._engine})()
    session.effective_settings = type("Settings", (), {"get": lambda self, key, default=None: None})()
    session.lifecycle = bus
    session._reminders = ReminderQueue()
    from agentos.agentic.workspace import ConversationWorkspace

    session.workspace = ConversationWorkspace.at_root(workspace)
    session._apply_working_directories()
    assert session.workspace.additional_roots == (other.resolve(),)
    added = [dict(event.payload) for event in events if event.name is HookEvent.DIRECTORY_ADDED]
    assert [item["directory"] for item in added] == [str(other.resolve())]
    assert any("working directories" in item.text for item in session._reminders.drain())
    events.clear()
    session._apply_working_directories()  # the next turn: still a working directory, not announced again
    assert session.workspace.additional_roots == (other.resolve(),)
    assert not [event for event in events if event.name is HookEvent.DIRECTORY_ADDED]


def _client(tmp_path: Path, store, *, server: bool = False):
    from fastapi.testclient import TestClient

    from agentos.api.gateway import ApiServices, create_app
    from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
    from agentos.configuration.capabilities import InstanceCapabilities
    from agentos.configuration.mode import RuntimeMode

    class _Conversations:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            return {"conversation_id": conversation_id, "title": "T", "state": "idle", "provider": "openai",
                    "model_id": "m", "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=_Conversations(),  # type: ignore[arg-type]
        workspace_root=tmp_path / "workspaces", conversation_directories=store,
        capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER if server else RuntimeMode.LOCAL),
    )
    return TestClient(create_app(services))


def test_the_api_adds_lists_and_removes(store_and_conversation, tmp_path: Path) -> None:
    store, conversation_id, _, other = store_and_conversation
    client = _client(tmp_path, store)
    headers = {"Authorization": "Bearer pat"}
    base = f"/v1/conversations/{conversation_id}/directories"
    created = client.post(base, json={"path": str(other)}, headers=headers)
    assert created.status_code == 201, created.text
    directory_id = created.json()["directory_id"]
    assert client.post(base, json={"path": str(other)}, headers=headers).status_code == 200
    assert [item["path"] for item in client.get(base, headers=headers).json()["items"]] == [str(other.resolve())]
    assert client.post(base, json={"path": str(Path.home())}, headers=headers).status_code == 422
    assert client.delete(f"{base}/{directory_id}", headers=headers).status_code == 204
    assert client.delete(f"{base}/{directory_id}", headers=headers).status_code == 404


def test_a_server_never_exposes_host_folders(store_and_conversation, tmp_path: Path) -> None:
    store, conversation_id, _, other = store_and_conversation
    client = _client(tmp_path, store, server=True)
    response = client.post(f"/v1/conversations/{conversation_id}/directories", json={"path": str(other)}, headers={"Authorization": "Bearer pat"})
    assert response.status_code == 404
    assert store.list("user-1", conversation_id) == []
