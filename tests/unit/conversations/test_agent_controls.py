"""Unit tests for AgentControlStore and InMemoryAgentControls."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from agentos.api.contracts import ApplicationValidationError
from agentos.conversations.agent_controls import AgentControlStore, InMemoryAgentControls
from agentos.persistence.postgres.schema import conversation_agent_controls, create_engine_for_tests


def _make_stores():
    engine = create_engine_for_tests()
    return [AgentControlStore(engine), InMemoryAgentControls()]


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_request_stop_is_idempotent_while_pending_and_new_after_taken(kind: str) -> None:
    engine = create_engine_for_tests()
    store = AgentControlStore(engine) if kind == "sql" else InMemoryAgentControls()
    agent_id = "agent:conv-1:0123456789abcdef01234567"

    first = store.request_stop("u1", "conv-1", agent_id)
    second = store.request_stop("u1", "conv-1", agent_id)
    assert first == second

    taken = store.take("u1", "conv-1", agent_id)
    assert len(taken) == 1
    assert taken[0]["control_id"] == first
    assert taken[0]["kind"] == "stop"
    assert taken[0]["content"] == ""

    store.set_outcome(first, "stopped")
    # Once consumed, a new stop request creates a fresh control_id.
    third = store.request_stop("u1", "conv-1", agent_id)
    assert third != first


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_post_message_strips_validates_and_takes_in_order(kind: str) -> None:
    engine = create_engine_for_tests()
    store = AgentControlStore(engine) if kind == "sql" else InMemoryAgentControls()
    agent_id = "agent:conv-1:abcdef0123456789abcdef01"

    m1 = store.post_message("u1", "conv-1", agent_id, "  primeiro aviso  ")
    m2 = store.post_message("u1", "conv-1", agent_id, "segundo aviso")

    with pytest.raises(ApplicationValidationError):
        store.post_message("u1", "conv-1", agent_id, "   ")
    with pytest.raises(ApplicationValidationError):
        store.post_message("u1", "conv-1", agent_id, "x" * 8001)

    taken = store.take("u1", "conv-1", agent_id)
    assert [item["control_id"] for item in taken] == [m1, m2]
    assert [item["content"] for item in taken] == ["primeiro aviso", "segundo aviso"]
    assert [item["kind"] for item in taken] == ["message", "message"]
    assert store.take("u1", "conv-1", agent_id) == []


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_expire_pending_marks_controls_expired_and_returns_them(kind: str) -> None:
    engine = create_engine_for_tests()
    store = AgentControlStore(engine) if kind == "sql" else InMemoryAgentControls()
    agent_id = "agent:conv-1:abcdef0123456789abcdef01"

    stop_id = store.request_stop("u1", "conv-1", agent_id)
    msg_id = store.post_message("u1", "conv-1", agent_id, "oi")

    expired = store.expire_pending("u1", "conv-1", agent_id)
    assert [item["control_id"] for item in expired] == [stop_id, msg_id]
    assert [item["outcome"] for item in expired] == ["expired", "expired"]
    assert store.take("u1", "conv-1", agent_id) == []

    if kind == "sql":
        with engine.connect() as conn:
            rows = conn.execute(select(conversation_agent_controls)).mappings().all()
        assert len(rows) == 2
        assert all(row["consumed_at"] is not None and row["outcome"] == "expired" for row in rows)


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_isolation_by_user_conversation_and_agent(kind: str) -> None:
    engine = create_engine_for_tests()
    store = AgentControlStore(engine) if kind == "sql" else InMemoryAgentControls()

    c1 = store.post_message("u1", "conv-1", "agent:conv-1:a", "para A")
    c2 = store.post_message("u1", "conv-1", "agent:conv-1:b", "para B")
    c3 = store.post_message("u2", "conv-1", "agent:conv-1:a", "outro user")
    c4 = store.post_message("u1", "conv-2", "agent:conv-1:a", "outra conversa")

    assert [x["control_id"] for x in store.take("u1", "conv-1", "agent:conv-1:a")] == [c1]
    assert [x["control_id"] for x in store.take("u1", "conv-1", "agent:conv-1:b")] == [c2]
    assert [x["control_id"] for x in store.take("u2", "conv-1", "agent:conv-1:a")] == [c3]
    assert [x["control_id"] for x in store.take("u1", "conv-2", "agent:conv-1:a")] == [c4]


def test_agent_control_routes_happy_path_not_found_conflict_and_validation(tmp_path) -> None:
    from pathlib import Path
    from fastapi.testclient import TestClient

    from agentos.agentic.settings import AgentRuntimeSettingsStore
    from agentos.api.gateway import ApiServices, create_app
    from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
    from agentos.conversations.chat import ChatApplication, PostgresChatStore
    from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
    from agentos.persistence.postgres.schema import metadata
    from agentos.persistence.sqlite import create_local_engine

    db_path = Path(tmp_path) / "controls_routes.db"
    engine = create_local_engine(f"sqlite:///{db_path.as_posix()}")
    metadata.create_all(engine)
    try:
        store = PostgresChatStore(engine)
        chat_app = ChatApplication(store)
        security = InMemorySecurityService()
        security.add_pat(
            "pat-u1",
            AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})),
        )
        security.add_pat(
            "pat-u2",
            AuthenticatedPrincipal(user_id="user-2", credential_ref="pat-2", scopes=frozenset({"api"})),
        )
        services = ApiServices(
            security=security,
            conversation_application=chat_app,
            agentic_runtime=AgentRuntimeSettingsStore(Path(tmp_path) / "runtime.json"),
            workspace_root=Path(tmp_path) / "workspaces",
        )
        client = TestClient(create_app(services))
        h1 = {"Authorization": "Bearer pat-u1"}
        h2 = {"Authorization": "Bearer pat-u2"}

        conv1 = chat_app.create(
            user_id="user-1",
            message="Tarefa A",
            provider="openrouter",
            model_id="m1",
            idempotency_key="idem-c1",
        ).conversation_id
        conv2 = chat_app.create(
            user_id="user-1",
            message="Tarefa B",
            provider="openrouter",
            model_id="m1",
            idempotency_key="idem-c2",
        ).conversation_id

        agents1 = ConversationAgentStore(engine, conversation_id=conv1, user_id="user-1")
        running_agent = agents1.create(
            "pesquisador",
            "Explore",
            provider="openrouter",
            model_id="m1",
            parent_agent_id="agent:main",
        )
        agents1.set_state(str(running_agent["agent_id"]), "working")

        completed_agent = agents1.create(
            "revisor",
            "Explore",
            provider="openrouter",
            model_id="m1",
            parent_agent_id="agent:main",
        )
        agents1.set_state(str(completed_agent["agent_id"]), "completed")

        agents2 = ConversationAgentStore(engine, conversation_id=conv2, user_id="user-1")
        other_conv_agent = agents2.create(
            "outro",
            "Explore",
            provider="openrouter",
            model_id="m1",
            parent_agent_id="agent:main",
        )
        agents2.set_state(str(other_conv_agent["agent_id"]), "running")

        # 202 happy path: stop
        r_stop = client.post(
            f"/v1/conversations/{conv1}/agents/{running_agent['agent_id']}/stop",
            headers=h1,
        )
        assert r_stop.status_code == 202, r_stop.text
        stop_body = r_stop.json()
        assert stop_body["agent_id"] == running_agent["agent_id"]
        assert stop_body["status"] == "stopping"
        assert stop_body["control_id"].startswith("ctrl_")

        # 202 happy path: message
        r_msg = client.post(
            f"/v1/conversations/{conv1}/agents/{running_agent['agent_id']}/messages",
            json={"message": "Use o arquivo B"},
            headers=h1,
        )
        assert r_msg.status_code == 202, r_msg.text
        msg_body = r_msg.json()
        assert msg_body["agent_id"] == running_agent["agent_id"]
        assert msg_body["status"] == "queued"
        assert msg_body["control_id"].startswith("ctrl_")

        # 404: agent belongs to another conversation of the same user
        r_wrong_conv = client.post(
            f"/v1/conversations/{conv1}/agents/{other_conv_agent['agent_id']}/stop",
            headers=h1,
        )
        assert r_wrong_conv.status_code == 404

        # 404: conversation belongs to another user
        r_wrong_user = client.post(
            f"/v1/conversations/{conv1}/agents/{running_agent['agent_id']}/stop",
            headers=h2,
        )
        assert r_wrong_user.status_code == 404

        # 409 AGENT_NOT_RUNNING: agent is completed
        r_completed_stop = client.post(
            f"/v1/conversations/{conv1}/agents/{completed_agent['agent_id']}/stop",
            headers=h1,
        )
        assert r_completed_stop.status_code == 409
        assert r_completed_stop.json()["error"]["code"] == "AGENT_NOT_RUNNING"

        r_completed_msg = client.post(
            f"/v1/conversations/{conv1}/agents/{completed_agent['agent_id']}/messages",
            json={"message": "oi"},
            headers=h1,
        )
        assert r_completed_msg.status_code == 409
        assert r_completed_msg.json()["error"]["code"] == "AGENT_NOT_RUNNING"

        # 422: empty or >8000 message
        r_empty = client.post(
            f"/v1/conversations/{conv1}/agents/{running_agent['agent_id']}/messages",
            json={"message": ""},
            headers=h1,
        )
        assert r_empty.status_code == 422

        r_blank = client.post(
            f"/v1/conversations/{conv1}/agents/{running_agent['agent_id']}/messages",
            json={"message": "   "},
            headers=h1,
        )
        assert r_blank.status_code == 422

        r_too_long = client.post(
            f"/v1/conversations/{conv1}/agents/{running_agent['agent_id']}/messages",
            json={"message": "a" * 8001},
            headers=h1,
        )
        assert r_too_long.status_code == 422
    finally:
        engine.dispose()



@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_withdraw_only_expires_a_control_nobody_took(kind: str) -> None:
    store = AgentControlStore(create_engine_for_tests()) if kind == "sql" else InMemoryAgentControls()
    pending = store.post_message("u1", "c1", "a1", "ainda pendente")
    assert store.withdraw(pending) is True
    assert store.take("u1", "c1", "a1") == []

    taken = store.post_message("u1", "c1", "a1", "já lida")
    assert [item["control_id"] for item in store.take("u1", "c1", "a1")] == [taken]
    assert store.withdraw(taken) is False


def test_take_never_returns_a_row_another_taker_claimed_first() -> None:
    """Between this taker's SELECT and its UPDATE another worker consumes the row: it must not be returned twice."""
    from datetime import UTC, datetime

    from sqlalchemy import event, update

    engine = create_engine_for_tests()
    store = AgentControlStore(engine)
    control_id = store.post_message("u1", "c1", "a1", "uma vez só")
    raced: list[bool] = []

    def competing_taker(conn, clauseelement, multiparams, params, execution_options):
        if getattr(clauseelement, "is_update", False) and not raced:
            raced.append(True)
            conn.execute(
                update(conversation_agent_controls)
                .where(conversation_agent_controls.c.control_id == control_id)
                .values(consumed_at=datetime.now(UTC), outcome="delivered")
            )
        return clauseelement, multiparams, params

    event.listen(engine, "before_execute", competing_taker, retval=True)
    try:
        assert store.take("u1", "c1", "a1") == []
    finally:
        event.remove(engine, "before_execute", competing_taker)
    assert raced == [True]
    with engine.connect() as conn:
        outcome = conn.execute(
            select(conversation_agent_controls.c.outcome).where(conversation_agent_controls.c.control_id == control_id)
        ).scalar()
    assert outcome == "delivered"
