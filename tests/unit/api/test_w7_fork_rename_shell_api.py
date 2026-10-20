"""W7: the fork, rename and shell routes, and per-message rewind of the conversation."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode

AUTH = {"Authorization": "Bearer test-pat"}


class _Application:
    def __init__(self, project_id: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.project_id = project_id

    def get(self, conversation_id, user_id):
        return {"conversation_id": conversation_id, "title": "T", "state": "completed", "provider": "openrouter", "model_id": "m",
                "mode": "code", "project_id": self.project_id, "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

    def fork(self, conversation_id, user_id, *, before_message_id=None, title=None):
        self.calls.append(("fork", conversation_id, before_message_id, title))
        return {"conversation_id": "chat_new", "title": title or "T (ramo)", "prompt": "segunda" if before_message_id else None,
                "messages": 2, "project_id": self.project_id, "source_conversation_id": conversation_id}

    def rename(self, conversation_id, user_id, title):
        self.calls.append(("rename", conversation_id, title))
        return {"conversation_id": conversation_id, "title": title.strip()}

    def shell(self, user_id, conversation_id, command, key):
        self.calls.append(("shell", conversation_id, command, key))
        return {"conversation_id": conversation_id, "turn_id": "turn_1", "message_id": "msg_1", "state": "queued"}


class _Roots:
    def __init__(self) -> None:
        self.roots = {"c1": "C:/Projetos/pagamentos"}

    def root_for(self, workspace_id, user_id):
        return self.roots.get(workspace_id)

    def set_root(self, workspace_id, user_id, root):
        self.roots[workspace_id] = root

    def clear_root(self, workspace_id, user_id):
        return self.roots.pop(workspace_id, None) is not None


def _client(tmp_path: Path, application: _Application, roots: _Roots | None = None, mode: RuntimeMode = RuntimeMode.LOCAL) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("test-pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=application,  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"), workspace_root=tmp_path / "workspaces",
        capabilities=InstanceCapabilities.for_mode(mode), local_workspaces=roots,
    )
    return TestClient(create_app(services))


def test_a_fork_outside_a_project_keeps_the_folder(tmp_path: Path) -> None:
    roots = _Roots()
    application = _Application()
    response = _client(tmp_path, application, roots).post("/v1/conversations/c1/fork", headers=AUTH, json={"title": "Ramo"})
    assert response.status_code == 201 and response.json()["conversation_id"] == "chat_new"
    assert roots.roots["chat_new"] == "C:/Projetos/pagamentos"
    assert application.calls == [("fork", "c1", None, "Ramo")]


def test_a_fork_in_a_project_shares_the_projects_folder(tmp_path: Path) -> None:
    roots = _Roots()
    _client(tmp_path, _Application(project_id="p1"), roots).post("/v1/conversations/c1/fork", headers=AUTH, json={})
    assert "chat_new" not in roots.roots


def test_rename_and_the_shell_turn(tmp_path: Path) -> None:
    application = _Application()
    client = _client(tmp_path, application, _Roots())
    assert client.patch("/v1/conversations/c1/title", headers=AUTH, json={"title": "Pagamentos"}).json()["title"] == "Pagamentos"
    assert client.post("/v1/conversations/c1/shell", headers=AUTH, json={"command": "git status"}).status_code == 422  # no key
    shell = client.post("/v1/conversations/c1/shell", headers={**AUTH, "Idempotency-Key": "s1"}, json={"command": "git status"})
    assert shell.status_code == 201 and shell.json()["turn_id"] == "turn_1"
    assert ("shell", "c1", "git status", "s1") in application.calls


def test_a_shared_server_has_no_shell_mode(tmp_path: Path) -> None:
    client = _client(tmp_path, _Application(), None, RuntimeMode.SERVER)
    assert client.post("/v1/conversations/c1/shell", headers={**AUTH, "Idempotency-Key": "s1"}, json={"command": "ls"}).status_code == 404


def test_rewinding_the_conversation_forks_it_before_the_message(tmp_path: Path) -> None:
    application = _Application()
    client = _client(tmp_path, application, _Roots())
    response = client.post("/v1/conversations/c1/messages/m3/rewind", headers={**AUTH, "Idempotency-Key": "r1"}, json={"mode": "conversation"})
    assert response.status_code == 200, response.text
    assert response.json()["fork"]["prompt"] == "segunda" and application.calls == [("fork", "c1", "m3", None)]
    assert client.post("/v1/conversations/c1/messages/m3/rewind", headers=AUTH, json={"mode": "everything"}).status_code == 422
