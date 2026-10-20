"""W7 Batch F: configuration-file MCP servers per scope, and approving a project's servers from the interface."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.settings.paths import orin_user_dir, workspace_grants_file

AUTH = {"Authorization": "Bearer test-pat"}
CLI = {"Authorization": "Bearer cli-token"}


class _Conversations:
    def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
        return {"conversation_id": conversation_id, "title": "T", "state": "idle", "provider": "openai", "model_id": "m",
                "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}


def _client(tmp_path: Path, mode: RuntimeMode = RuntimeMode.LOCAL) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("test-pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    security.add_pat("cli-token", AuthenticatedPrincipal(user_id="local-user", credential_ref="cli", scopes=frozenset({"api"}),
                                                         credential_kind="cli_token"))
    services = ApiServices(
        security=security, conversation_application=_Conversations(),  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"), workspace_root=tmp_path / "workspaces",
        capabilities=InstanceCapabilities.for_mode(mode),
    )
    return TestClient(create_app(services))


def _folder(client: TestClient, *, trusted: bool) -> Path:
    root = Path(client.get("/v1/conversations/c1/workspace-trust", headers=AUTH).json()["path"])
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github", "--token", "${GH_TOKEN}"]},
        "docs": {"type": "http", "url": "https://docs.example.com/mcp?key=${DOCS_KEY}"},
    }}), encoding="utf-8")
    if trusted:
        client.post("/v1/conversations/c1/workspace-trust", headers={**AUTH, "Idempotency-Key": "trust-1"})
    return root


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_supersecretvalue1234567890")
    monkeypatch.setenv("DOCS_KEY", "docs-secret-key-0987654321")


def _approve(client: TestClient, server: str, decision: str = "approve", headers: dict[str, str] = AUTH):
    return client.put("/v1/settings/mcp/approval", headers=headers, json={"conversation_id": "c1", "server": server, "decision": decision})


def test_an_untrusted_folder_s_servers_wait_for_trust_and_cannot_be_approved(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _folder(client, trusted=False)
    body = client.get("/v1/settings/mcp", params={"conversation_id": "c1"}, headers=AUTH).json()
    assert body["awaiting_trust"] is True and body["trusted"] is False
    assert body["pending"] == [] and [item for item in body["servers"] if item["scope"] == "project"] == []
    refused = _approve(client, "github")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "folder_not_trusted"


def test_a_trusted_folder_lists_its_servers_as_written_and_never_an_expanded_secret(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _folder(client, trusted=True)
    response = client.get("/v1/settings/mcp", params={"conversation_id": "c1"}, headers=AUTH)
    text = response.text
    assert "ghp_supersecretvalue" not in text and "docs-secret-key" not in text
    pending = {item["name"]: item for item in response.json()["pending"]}
    assert set(pending) == {"github", "docs"}
    # what looks like a secret is masked even as a placeholder: a token pasted into the repository would be too
    assert pending["github"]["shown"] == "npx -y @modelcontextprotocol/server-github --token [REDACTED]"
    assert pending["docs"]["shown"] == "https://docs.example.com/mcp?key=${DOCS_KEY}"
    assert pending["github"]["state"] == "pending" and pending["github"]["source"].endswith(".mcp.json")


def test_approving_and_denying_write_only_the_folder_s_grants_file(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = _folder(client, trusted=True)
    approved = _approve(client, "github")
    assert approved.status_code == 200
    states = {item["name"]: item["state"] for item in approved.json()["servers"] + approved.json()["pending"]}
    assert states == {"github": "on", "docs": "pending"}
    grants = json.loads(workspace_grants_file(root).read_text(encoding="utf-8"))
    assert grants["enabledMcpjsonServers"] == ["github"] and "disabledMcpjsonServers" not in grants
    assert not (root / ".orin" / "settings.json").exists() and not (root / ".orin" / "settings.local.json").exists()

    denied = _approve(client, "github", "deny").json()
    assert {item["name"]: item["state"] for item in denied["servers"]}["github"] == "off"
    grants = json.loads(workspace_grants_file(root).read_text(encoding="utf-8"))
    assert grants == {"disabledMcpjsonServers": ["github"]}  # a denial replaces the approval, never both
    assert denied["choices"] == {"disabledMcpjsonServers": ["github"]}

    reset = _approve(client, "github", "reset").json()
    assert {item["name"] for item in reset["pending"]} == {"github", "docs"}
    assert json.loads(workspace_grants_file(root).read_text(encoding="utf-8")) == {}


def test_only_the_folder_s_own_servers_can_be_approved(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _folder(client, trusted=True)
    refused = _approve(client, "evil")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "unknown_server"
    assert _approve(client, "github", "maybe").status_code == 422


def test_the_person_s_own_servers_show_only_their_program_or_address(tmp_path: Path) -> None:
    (orin_user_dir()).mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "mcp.json").write_text(json.dumps({"mcpServers": {
        "notes": {"command": "C:/tools/notes-server.exe", "args": ["--key", "${GH_TOKEN}"]},
        "remote": {"type": "http", "url": "https://user:pass@mcp.example.com:8443/v1/secret-path?token=abc"},
    }}), encoding="utf-8")
    body = _client(tmp_path).get("/v1/settings/mcp", headers=AUTH)
    assert "ghp_supersecretvalue" not in body.text and "secret-path" not in body.text and "pass@" not in body.text
    shown = {item["name"]: (item["scope"], item["shown"]) for item in body.json()["servers"]}
    assert shown == {"notes": ("user", "notes-server.exe"), "remote": ("user", "https://mcp.example.com:8443")}


def test_a_shared_server_and_orin_code_s_token_cannot_use_the_editor(tmp_path: Path) -> None:
    assert _client(tmp_path, RuntimeMode.SERVER).get("/v1/settings/mcp", headers=AUTH).status_code == 404
    client = _client(tmp_path)
    _folder(client, trusted=True)
    assert _approve(client, "github", headers=CLI).status_code == 403
