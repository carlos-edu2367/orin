"""TDD tests for POST /v1/conversations/{conversation_id}/worktrees/{agent_id}/discard."""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.code_mode.agent_worktree import AgentWorktreeManager
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.persistence.postgres.schema import metadata

AUTH_U1 = {"Authorization": "Bearer pat-1"}
AUTH_U2 = {"Authorization": "Bearer pat-2"}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "orin-test@example.com")
    _git(repo, "config", "user.name", "Orin Test")
    (repo / ".gitignore").write_text(".orin/\n", encoding="utf-8")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "file.txt")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture()
def api_env(tmp_path: Path):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    chat_store = PostgresChatStore(engine)
    chat_app = ChatApplication(chat_store)
    created = chat_app.create(
        user_id="user-1",
        message="Worktree Discard Test",
        provider="openai",
        model_id="gpt-4o",
        idempotency_key="idem-wt-discard-1",
    )
    conv_id = created.conversation_id
    ws_root = tmp_path / "workspaces"
    repo = _init_repo(ConversationWorkspace(ws_root, conv_id).root)
    security = InMemorySecurityService()
    security.add_pat("pat-1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    security.add_pat("pat-2", AuthenticatedPrincipal("user-2", "cred-2", frozenset({"api"})))
    app = create_app(
        ApiServices(
            security=security,
            conversation_application=chat_app,
            workspace_root=ws_root,
        )
    )
    client = TestClient(app)
    yield client, engine, repo, conv_id
    engine.dispose()


def test_discard_worktree_route_discards_and_updates_store(api_env) -> None:
    client, engine, repo, conv_id = api_env
    wt_store = AgentWorktreeStore(engine)
    mgr = AgentWorktreeManager(repo, store=wt_store)
    wt = mgr.create_for_agent("child-conflict-1")
    (wt.path / "file.txt").write_text("v2 conflicting\n", encoding="utf-8")
    wt_store.update_merge_state("child-conflict-1", "conflict", merge_detail={"files": ["file.txt"]})

    # Another user cannot discard user-1's conversation worktree.
    forbidden = client.post(f"/v1/conversations/{conv_id}/worktrees/child-conflict-1/discard", headers=AUTH_U2)
    assert forbidden.status_code in {403, 404}
    assert wt.path.is_dir()

    resp = client.post(f"/v1/conversations/{conv_id}/worktrees/child-conflict-1/discard", headers=AUTH_U1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["discarded"] is True
    assert body["agent_id"] == "child-conflict-1"
    assert body["merge_state"] == "discarded"
    assert not wt.path.exists()
    assert wt_store.get("child-conflict-1").merge_state == "discarded"


def test_discard_worktree_route_returns_404_for_unknown_agent(api_env) -> None:
    client, _engine, _repo, conv_id = api_env
    resp = client.post(f"/v1/conversations/{conv_id}/worktrees/nonexistent-agent/discard", headers=AUTH_U1)
    assert resp.status_code == 404
