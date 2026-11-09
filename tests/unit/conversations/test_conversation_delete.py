"""Deleting a conversation removes it and every row that only existed for it, refuses while it runs and never
touches the Orin Friend; memories and the person's schedules survive, detached."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, insert, select, update
from sqlalchemy.pool import StaticPool

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.contracts import ApplicationConflictError, ApplicationNotFoundError
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.hooks import session_end
from agentos.persistence.postgres.schema import (
    agent_memories, conversation_activity_events, conversation_dispatches, conversation_messages, conversation_turns, conversations,
    metadata, persistence_clock, scheduled_chat_tasks, schedules,
)
from agentos.provider_catalog.models import ProviderCatalogContext

from tests.unit.conversations.test_chat_application import _store
from tests.unit.conversations.test_w7_fork_rename_shell import _answered

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def _finished(store: PostgresChatStore, user_id: str = "user-1", key: str = "k1") -> tuple[ChatApplication, str]:
    application = ChatApplication(store)
    receipt = application.create(
        ProviderCatalogContext(user_id, "conversation.create"), message="pergunta", provider="openrouter",
        model_id="model-a", workspace_id=None, idempotency_key=key, code_mode="code",
    )
    _answered(store, receipt.conversation_id, "resposta")
    with store._engine.begin() as c:
        c.execute(update(conversations).where(conversations.c.conversation_id == receipt.conversation_id).values(state="completed"))
    return application, receipt.conversation_id


def _activity(store: PostgresChatStore, conversation_id: str, user_id: str = "user-1") -> None:
    with store._engine.begin() as c:
        turn = c.execute(select(conversation_turns).where(conversation_turns.c.conversation_id == conversation_id)).mappings().first()
        c.execute(insert(conversation_activity_events).values(
            event_id=f"evt-{conversation_id}", conversation_id=conversation_id, user_id=user_id, turn_id=turn["turn_id"],
            execution_id=turn["execution_id"], agent_id="main", event_type="tool.started", sequence=1, summary="s", payload={},
            visibility="user", created_at=NOW,
        ))


def _schedule(store: PostgresChatStore, schedule_id: str, conversation_id: str, source: str) -> None:
    with store._engine.begin() as c:
        c.execute(insert(schedules).values(
            schedule_id=schedule_id, user_id="user-1", agent_id="orin", schedule_type="chat", target={}, rule={"kind": "once", "source": source},
            timezone="UTC", policies={}, state="ACTIVE", version=1, next_fire_at=NOW, starts_at=NOW, idempotency_key=schedule_id,
            created_at=NOW, updated_at=NOW,
        ))
        c.execute(insert(scheduled_chat_tasks).values(
            task_id=f"task-{schedule_id}", schedule_id=schedule_id, user_id="user-1", message="m", provider="openrouter", model_id="model-a",
            conversation_id=conversation_id, created_at=NOW, updated_at=NOW,
        ))


def _count(store: PostgresChatStore, table, conversation_id: str) -> int:
    with store._engine.connect() as c:
        return int(c.execute(select(func.count()).select_from(table).where(table.c.conversation_id == conversation_id)).scalar_one())


def test_delete_removes_the_conversation_and_its_rows_but_keeps_memories_and_the_persons_schedules() -> None:
    store = _store()
    application, conversation_id = _finished(store)
    _, other_id = _finished(store, key="k-other")
    _activity(store, conversation_id)
    _activity(store, other_id)
    _schedule(store, "sch-user", conversation_id, "user")
    _schedule(store, "sch-wakeup", conversation_id, "wakeup")
    with store._engine.begin() as c:
        c.execute(insert(agent_memories).values(memory_id="mem-1", user_id="user-1", conversation_id=conversation_id, fact="gosta de café",
                                                tags=[], created_at=NOW, updated_at=NOW))
        turn_ids = list(c.execute(select(conversation_turns.c.turn_id).where(conversation_turns.c.conversation_id == conversation_id)).scalars())
    session_end.register_session(conversation_id, workspace_root=Path("."), user_id="user-1", trusted=False)

    result = application.delete(conversation_id, "user-1")

    assert result["deleted"] is True and result["cancelled_schedules"] == 1
    for table in (conversations, conversation_messages, conversation_turns, conversation_activity_events):
        assert _count(store, table, conversation_id) == 0, table.name
        assert _count(store, table, other_id) > 0, table.name  # the neighbour is untouched
    with store._engine.connect() as c:
        assert c.execute(select(conversation_dispatches).where(conversation_dispatches.c.turn_id.in_(turn_ids))).first() is None
        memory = c.execute(select(agent_memories).where(agent_memories.c.memory_id == "mem-1")).mappings().one()
        tasks = {row.schedule_id: row.conversation_id for row in c.execute(select(scheduled_chat_tasks))}
        states = {row.schedule_id: row.state for row in c.execute(select(schedules))}
    assert memory["conversation_id"] is None and memory["fact"] == "gosta de café"
    assert tasks == {"sch-user": None, "sch-wakeup": None}
    assert states == {"sch-user": "ACTIVE", "sch-wakeup": "CANCELLED"}
    assert conversation_id not in session_end._REGISTRY
    with pytest.raises(ApplicationNotFoundError):
        application.get(conversation_id, "user-1")


def test_someone_elses_conversation_is_not_found_and_untouched() -> None:
    store = _store()
    application, conversation_id = _finished(store)
    with pytest.raises(ApplicationNotFoundError):
        application.delete(conversation_id, "someone-else")
    with pytest.raises(ApplicationNotFoundError):
        application.delete("chat_missing", "user-1")
    assert _count(store, conversations, conversation_id) == 1 and _count(store, conversation_messages, conversation_id) == 2


def test_a_running_conversation_cannot_be_deleted() -> None:
    store = _store()
    application = ChatApplication(store)
    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"), message="pergunta", provider="openrouter",
        model_id="model-a", workspace_id=None, idempotency_key="k1",
    )  # the turn is queued
    with pytest.raises(ApplicationConflictError) as error:
        application.delete(receipt.conversation_id, "user-1")
    assert error.value.code == "conversation_running" and "pare a sessão" in str(error.value)
    with store._engine.begin() as c:
        c.execute(update(conversation_turns).where(conversation_turns.c.conversation_id == receipt.conversation_id).values(state="running"))
    with pytest.raises(ApplicationConflictError):
        application.delete(receipt.conversation_id, "user-1")
    assert _count(store, conversations, receipt.conversation_id) == 1


def test_the_friend_conversation_is_never_deleted() -> None:
    store = _store()
    application = ChatApplication(store)
    friend = application.ensure_friend_conversation("user-1")
    with pytest.raises(ApplicationConflictError) as error:
        application.delete(str(friend["conversation_id"]), "user-1")
    assert error.value.code == "conversation_protected"
    assert _count(store, conversations, str(friend["conversation_id"])) == 1


def _shared_store() -> PostgresChatStore:
    """The route runs the store in a worker thread: one in-memory database shared by every connection."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(persistence_clock.insert().values(id=1, revision=0))
    return PostgresChatStore(engine)


def _client(tmp_path: Path, application: ChatApplication) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("test-pat", AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=application,
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"), workspace_root=tmp_path / "workspaces",
        capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL),
    )
    return TestClient(create_app(services))


def test_the_route_answers_204_and_the_conversation_leaves_the_list(tmp_path: Path) -> None:
    store = _shared_store()
    application, conversation_id = _finished(store)
    client = _client(tmp_path, application)
    auth = {"Authorization": "Bearer test-pat"}
    listed = lambda: [item["conversation_id"] for item in client.get("/v1/conversations", headers=auth).json()["items"]]  # noqa: E731
    assert conversation_id in listed()

    response = client.delete(f"/v1/conversations/{conversation_id}", headers=auth)
    assert response.status_code == 204 and response.content == b""
    assert conversation_id not in listed()
    assert client.delete(f"/v1/conversations/{conversation_id}", headers=auth).status_code == 404


def test_the_route_answers_409_with_a_message_while_running_and_for_the_friend(tmp_path: Path) -> None:
    store = _shared_store()
    application = ChatApplication(store)
    running = application.create(
        ProviderCatalogContext("user-1", "conversation.create"), message="pergunta", provider="openrouter",
        model_id="model-a", workspace_id=None, idempotency_key="k1",
    )
    friend = application.ensure_friend_conversation("user-1")
    client = _client(tmp_path, application)
    auth = {"Authorization": "Bearer test-pat"}
    busy = client.delete(f"/v1/conversations/{running.conversation_id}", headers=auth)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "conversation_running" and "pare a sessão" in busy.json()["error"]["details"]["message"]
    protected = client.delete(f"/v1/conversations/{friend['conversation_id']}", headers=auth)
    assert protected.status_code == 409 and protected.json()["error"]["code"] == "conversation_protected"
