"""WS-B4 tests: HTTP routes GET /v1/conversations/{id}/agent-runs and POST .../agents/{agent_id}/resume."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.agent_run_persistence import AgentRunRecorder
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


def _setup_api(tmp_path: Path):
    db_path = tmp_path / "agent_run_routes.db"
    engine = create_local_engine(f"sqlite:///{db_path.as_posix()}")
    metadata.create_all(engine)
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
    return engine, store, chat_app, TestClient(app)


def test_list_agent_runs_and_resume_lifecycle_and_conflict_and_isolation(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "agent-runs"
    monkeypatch.setenv("ORIN_AGENT_RUNS_DIR", str(runs_dir))

    engine, store, chat_app, client = _setup_api(tmp_path)
    try:
        c1 = chat_app.create(
            user_id="user-1",
            message="inicie c1",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-runs-1",
        )
        c2 = chat_app.create(
            user_id="user-1",
            message="inicie c2",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-runs-2",
        )
        conv1, conv2 = c1.conversation_id, c2.conversation_id

        agents1 = ConversationAgentStore(engine, conversation_id=conv1, user_id="user-1")
        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)

        def _seed_agent(name: str, state: str, *, with_history: bool = True) -> str:
            rec = agents1.create(name, "general", parent_agent_id=f"agent:{conv1}:main", force_new=True)
            aid = str(rec["agent_id"])
            agents1.set_state(aid, state)
            recorder.start(
                agent_id=aid,
                user_id="user-1",
                conversation_id=conv1,
                turn_id=c1.turn_id,
                parent_agent_id=f"agent:{conv1}:main",
                subagent_type="general",
                provider="openai",
                model_id="gpt-4o",
                task=f"Task for {name}",
                state=state,
                initial_history=[{"role": "user", "content": f"Prior message for {name}"}] if with_history else None,
            )
            return aid

        interrupted_id = _seed_agent("worker-interrupted", "interrupted")
        completed_id = _seed_agent("worker-completed", "completed")
        failed_id = _seed_agent("worker-failed", "failed")
        cancelled_id = _seed_agent("worker-cancelled", "cancelled")
        running_id = _seed_agent("worker-running", "running")

        # 1) GET /v1/conversations/{id}/agent-runs lists all 5 runs for owner
        list_res = client.get(
            f"/v1/conversations/{conv1}/agent-runs",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert list_res.status_code == 200, list_res.text
        body = list_res.json()
        assert body["conversation_id"] == conv1
        by_id = {row["agent_id"]: row for row in body["runs"]}
        assert set(by_id.keys()) == {interrupted_id, completed_id, failed_id, cancelled_id, running_id}
        assert by_id[interrupted_id]["state"] == "interrupted"

        # 2) Isolation on GET: other user gets 404
        other_list = client.get(
            f"/v1/conversations/{conv1}/agent-runs",
            headers={"Authorization": "Bearer pat-u2"},
        )
        assert other_list.status_code == 404

        # 3) POST resume on non-interrupted states -> 409 Conflict
        for non_resumable_id in (completed_id, failed_id, cancelled_id, running_id):
            conflict_res = client.post(
                f"/v1/conversations/{conv1}/agents/{non_resumable_id}/resume",
                headers={"Authorization": "Bearer pat-u1"},
            )
            assert conflict_res.status_code == 409, (non_resumable_id, conflict_res.status_code, conflict_res.text)

        # 4) Isolation on POST resume: wrong conversation (404) and wrong user (404/403)
        wrong_conv = client.post(
            f"/v1/conversations/{conv2}/agents/{interrupted_id}/resume",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert wrong_conv.status_code in (403, 404)

        wrong_user = client.post(
            f"/v1/conversations/{conv1}/agents/{interrupted_id}/resume",
            headers={"Authorization": "Bearer pat-u2"},
        )
        assert wrong_user.status_code in (403, 404)

        # 5) POST resume on interrupted run -> 202 Accepted, transitions state to queued
        ok_res = client.post(
            f"/v1/conversations/{conv1}/agents/{interrupted_id}/resume",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert ok_res.status_code == 202, ok_res.text
        ok_body = ok_res.json()
        assert ok_body["agent_id"] == interrupted_id
        assert ok_body["conversation_id"] == conv1
        assert ok_body["state"] == "queued"
        assert ok_body["resumed"] is True
        assert ok_body["history_messages"] >= 1

        updated = AgentRunStore(engine).get(interrupted_id)
        assert updated is not None
        assert updated.state == "queued"
    finally:
        engine.dispose()


def test_resume_route_handles_corrupted_history_with_warning(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "agent-runs"
    monkeypatch.setenv("ORIN_AGENT_RUNS_DIR", str(runs_dir))

    engine, store, chat_app, client = _setup_api(tmp_path)
    try:
        c1 = chat_app.create(
            user_id="user-1",
            message="inicie corrupt",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-runs-corrupt",
        )
        conv1 = c1.conversation_id
        agents1 = ConversationAgentStore(engine, conversation_id=conv1, user_id="user-1")
        rec = agents1.create("worker-corrupt", "general", parent_agent_id=f"agent:{conv1}:main")
        aid = str(rec["agent_id"])
        agents1.set_state(aid, "interrupted")

        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        recorder.start(
            agent_id=aid,
            user_id="user-1",
            conversation_id=conv1,
            turn_id=c1.turn_id,
            subagent_type="general",
            state="interrupted",
        )
        recorder.history_path_for(aid).write_text("{{{not valid json", encoding="utf-8")

        res = client.post(
            f"/v1/conversations/{conv1}/agents/{aid}/resume",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert res.status_code == 202, res.text
        body = res.json()
        assert body["resumed"] is True
        assert body["history_messages"] == 0
        assert body["warning"] is not None
    finally:
        engine.dispose()
