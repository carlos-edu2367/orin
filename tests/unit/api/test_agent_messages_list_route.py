"""WS-B7: GET /v1/conversations/{conversation_id}/agents/{agent_id}/messages route tests."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


def test_list_agent_messages_route_returns_ordered_states_and_enforces_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "list_agent_msgs.db"
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
        app = create_app(
            ApiServices(
                security=security,
                conversation_application=chat_app,
                agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
                workspace_root=tmp_path / "workspaces",
            )
        )
        client = TestClient(app)

        created = chat_app.create(
            user_id="user-1",
            message="inicie",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-list-msgs-1",
        )
        conv_id = created.conversation_id
        other_created = chat_app.create(
            user_id="user-1",
            message="outra",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-list-msgs-2",
        )
        other_conv_id = other_created.conversation_id

        agents = ConversationAgentStore(engine, conversation_id=conv_id, user_id="user-1")
        agent_rec = agents.create(
            "worker",
            "worker",
            provider="openai",
            model_id="gpt-4o",
            parent_agent_id=f"agent:{conv_id}:main",
        )
        agent_id = str(agent_rec["agent_id"])
        agents.set_state(agent_id, "working")

        # Post user message via HTTP POST route (status 202 -> queued).
        post_res = client.post(
            f"/v1/conversations/{conv_id}/agents/{agent_id}/messages",
            headers={"Authorization": "Bearer pat-u1"},
            json={"message": "foca no modulo A"},
        )
        assert post_res.status_code == 202, post_res.text
        c1 = post_res.json()["control_id"]

        # Post parent message directly via control store and mark delivered.
        c2 = store._agent_controls().post_message(
            "user-1",
            conv_id,
            agent_id,
            "pare de editar X",
            sender_kind="parent",
            sender_agent_id=f"agent:{conv_id}:main",
        )
        store._agent_controls().take("user-1", conv_id, agent_id)
        store._agent_controls().set_outcome(c1, "injected")
        store._agent_controls().mark_delivered(c2)

        # GET list as owner returns both messages in order with exact fields.
        get_res = client.get(
            f"/v1/conversations/{conv_id}/agents/{agent_id}/messages",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert get_res.status_code == 200, get_res.text
        body = get_res.json()
        assert body["conversation_id"] == conv_id
        assert body["agent_id"] == agent_id
        messages = body["messages"]
        assert [m["control_id"] for m in messages] == [c1, c2]

        assert messages[0]["agent_id"] == agent_id
        assert messages[0]["sender_kind"] == "user"
        assert messages[0]["sender_agent_id"] is None
        assert messages[0]["message"] == "foca no modulo A"
        assert messages[0]["preview"] == "foca no modulo A"
        assert messages[0]["state"] == "injected"
        assert messages[0]["created_at"] is not None
        assert messages[0]["delivered_at"] is None

        assert messages[1]["sender_kind"] == "parent"
        assert messages[1]["sender_agent_id"] == f"agent:{conv_id}:main"
        assert messages[1]["message"] == "pare de editar X"
        assert messages[1]["state"] == "delivered"
        assert messages[1]["delivered_at"] is not None

        # Isolation: another user gets 404.
        other_user = client.get(
            f"/v1/conversations/{conv_id}/agents/{agent_id}/messages",
            headers={"Authorization": "Bearer pat-u2"},
        )
        assert other_user.status_code == 404

        # Isolation: another conversation of same user cannot see agent_id from conv_id (404).
        wrong_conv = client.get(
            f"/v1/conversations/{other_conv_id}/agents/{agent_id}/messages",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert wrong_conv.status_code == 404
    finally:
        engine.dispose()
