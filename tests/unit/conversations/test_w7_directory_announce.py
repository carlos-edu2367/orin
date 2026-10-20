"""W7 Batch F (W4): a folder the turn cannot use is not marked announced, so DirectoryAdded fires when it can."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.conversations.chat import PostgresChatStore
from agentos.conversations.directories import ConversationDirectoryStore
from agentos.persistence.postgres.schema import metadata


@pytest.fixture()
def setup(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'orin.db').as_posix()}", future=True)
    metadata.create_all(engine)
    receipt = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="m", idempotency_key="c1", code_mode="code")
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    store = ConversationDirectoryStore(engine)
    store.add("user-1", receipt.conversation_id, str(other), workspace_root=workspace)
    return store, receipt.conversation_id, workspace, other


def test_only_the_usable_folders_are_taken(setup) -> None:
    store, conversation_id, _, other = setup
    assert store.take_unannounced("user-1", conversation_id, lambda path: False) == []
    assert store.take_unannounced("user-1", conversation_id, lambda path: True) == [str(other.resolve())]
    assert store.take_unannounced("user-1", conversation_id, lambda path: True) == []


def test_a_folder_missing_for_one_turn_is_announced_on_the_next(setup) -> None:
    from agentos.agentic.lifecycle import HookEvent, LifecycleBus
    from agentos.agentic.reminders import ReminderQueue
    from agentos.agentic.session import TurnSession
    from agentos.agentic.workspace import ConversationWorkspace

    store, conversation_id, workspace, other = setup
    events = []
    bus = LifecycleBus()
    bus.subscribe(events.append)
    session = TurnSession.__new__(TurnSession)
    session.turn = {"user_id": "user-1", "conversation_id": conversation_id}
    session.host_folders = True
    session.chat_store = type("Store", (), {"_engine": store._engine})()
    session.effective_settings = type("Settings", (), {"get": lambda self, key, default=None: None})()
    session.lifecycle = bus
    session._reminders = ReminderQueue()
    session.workspace = ConversationWorkspace.at_root(workspace)

    away = other.with_name("other-away")
    other.rename(away)  # the folder is gone for this turn
    session._apply_working_directories()
    assert not [event for event in events if event.name is HookEvent.DIRECTORY_ADDED]

    away.rename(other)  # back for the next turn
    session.workspace = ConversationWorkspace.at_root(workspace)
    session._apply_working_directories()
    added = [dict(event.payload)["directory"] for event in events if event.name is HookEvent.DIRECTORY_ADDED]
    assert added == [str(other.resolve())]
