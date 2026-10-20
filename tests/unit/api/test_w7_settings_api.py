"""W7: the settings API the interface's editors use (host files: local profile only)."""
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
from agentos.settings.paths import orin_managed_dir, orin_user_dir

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


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_effective_settings_carry_provenance_per_rule_and_hide_secrets(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"permissions": {"allow": ["Bash(git status)", "Read"]}, "env": {"API_TOKEN": "s3cret"}})
    _write(orin_managed_dir() / "managed-settings.json", {"permissions": {"deny": ["Bash(rm:*)"], "allow": ["Read"]}})
    body = _client(tmp_path).get("/v1/settings/effective", headers=AUTH).json()
    assert "s3cret" not in json.dumps(body)
    allow = body["permissions"]["allow"]
    assert [(item["rule"], item["layer"], item["duplicate"]) for item in allow] == [
        ("Read", "managed", False), ("Bash(git status)", "user", False), ("Read", "user", True)]
    assert body["permissions"]["deny"][0]["layer"] == "managed"
    assert body["writable"]["user"].endswith("settings.json") and body["writable"]["project"] is None


def test_a_rule_is_added_and_removed_in_the_chosen_layer(tmp_path: Path) -> None:
    client = _client(tmp_path)
    added = client.put("/v1/settings/permissions", headers=AUTH, json={"layer": "user", "kind": "deny", "rule": "WebFetch", "op": "add"})
    assert added.status_code == 200 and added.json()["permissions"]["deny"][0]["rule"] == "WebFetch"
    assert json.loads((orin_user_dir() / "settings.json").read_text())["permissions"]["deny"] == ["WebFetch"]
    removed = client.put("/v1/settings/permissions", headers=AUTH, json={"layer": "user", "kind": "deny", "rule": "WebFetch", "op": "remove"})
    assert removed.json()["permissions"]["deny"] == []
    assert "permissions" not in json.loads((orin_user_dir() / "settings.json").read_text())


def test_project_and_local_rules_need_a_conversation_and_land_in_its_folder(tmp_path: Path) -> None:
    client = _client(tmp_path)
    refused = client.put("/v1/settings/permissions", headers=AUTH, json={"layer": "project", "kind": "allow", "rule": "Read", "op": "add"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "project_required"
    saved = client.put("/v1/settings/permissions", headers=AUTH,
                       json={"conversation_id": "c1", "layer": "project", "kind": "allow", "rule": "Bash(npm test)", "op": "add"})
    assert saved.status_code == 200
    root = Path(client.get("/v1/conversations/c1/workspace-trust", headers=AUTH).json()["path"])
    assert json.loads((root / ".orin" / "settings.json").read_text())["permissions"]["allow"] == ["Bash(npm test)"]


def test_an_invalid_rule_is_refused_with_its_reason(tmp_path: Path) -> None:
    response = _client(tmp_path).put("/v1/settings/permissions", headers=AUTH, json={"layer": "user", "kind": "allow", "rule": "Bash(", "op": "add"})
    assert response.status_code == 422 and response.json()["error"]["details"]["problems"]


def test_hooks_are_listed_and_saved_after_validation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    hooks = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo checked"}]}]}
    saved = client.put("/v1/settings/hooks", headers=AUTH, json={"layer": "user", "hooks": hooks})
    assert saved.status_code == 200
    body = saved.json()
    assert body["editable"]["user"]["hooks"] == hooks
    assert [(item["event"], item["matcher"], item["source"]) for item in body["effective"]] == [("PreToolUse", "Bash", "user")]
    bad = client.put("/v1/settings/hooks", headers=AUTH, json={"layer": "user", "hooks": {"PreToolUse": [{"hooks": [{"type": "nope"}]}]}})
    assert bad.status_code == 422
    callback = client.put("/v1/settings/hooks", headers=AUTH,
                          json={"layer": "user", "hooks": {"PreToolUse": [{"hooks": [{"type": "callback", "callback_id": "x"}]}]}})
    assert callback.status_code == 422
    assert json.loads((orin_user_dir() / "settings.json").read_text())["hooks"] == hooks  # the refused saves changed nothing


def test_an_agent_is_written_read_back_and_deleted(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {"scope": "user", "description": "Reviews: carefully", "prompt": "Review the diff.", "tools": ["Read", "Grep", "Bash"],
            "model": "sonnet", "max_turns": 5, "effort": "high"}
    saved = client.put("/v1/settings/agents/code-reviewer", headers=AUTH, json=body)
    assert saved.status_code == 200, saved.text
    agent = next(item for item in saved.json()["agents"] if item["name"] == "code-reviewer")
    assert agent["editable"] and agent["description"] == "Reviews: carefully" and agent["prompt"] == "Review the diff."
    assert agent["tools"] == ["Read", "Grep", "Bash"]
    assert client.put("/v1/settings/agents/x", headers=AUTH, json={**body, "tools": ["Bash(git diff:*)"]}).status_code == 422
    assert agent["model"] == "sonnet" and agent["effort"] == "high"
    assert client.put("/v1/settings/agents/verifier", headers=AUTH, json=body).status_code == 422  # a builtin name
    assert client.put("/v1/settings/agents/Bad Name", headers=AUTH, json=body).status_code == 422
    deleted = client.delete("/v1/settings/agents/code-reviewer?scope=user", headers=AUTH)
    assert deleted.status_code == 200 and all(item["name"] != "code-reviewer" for item in deleted.json()["agents"])
    assert client.delete("/v1/settings/agents/code-reviewer?scope=user", headers=AUTH).status_code == 404


def test_output_style_choice_is_checked_and_saved(tmp_path: Path) -> None:
    client = _client(tmp_path)
    listed = client.get("/v1/settings/output-styles", headers=AUTH).json()
    assert {"default", "explanatory", "learning"} <= {item["key"] for item in listed["styles"]} and listed["active"] == "default"
    chosen = client.put("/v1/settings/output-styles", headers=AUTH, json={"layer": "user", "style": "Explanatory"})
    assert chosen.status_code == 200 and chosen.json()["active"] == "explanatory" and chosen.json()["active_layer"] == "user"
    assert client.put("/v1/settings/output-styles", headers=AUTH, json={"layer": "user", "style": "nope"}).status_code == 422


def test_keybindings_are_stored_only_when_they_are_a_list(tmp_path: Path) -> None:
    client = _client(tmp_path)
    text = json.dumps({"bindings": [{"context": "Chat", "bindings": {"ctrl+k": "chat:submit"}}]})
    saved = client.put("/v1/settings/keybindings", headers=AUTH, json={"text": text})
    assert saved.status_code == 200 and saved.json()["orin"]["text"] == text
    assert client.put("/v1/settings/keybindings", headers=AUTH, json={"text": "{nope"}).status_code == 422
    assert client.put("/v1/settings/keybindings", headers=AUTH, json={"text": '{"bindings": 3}'}).status_code == 422
    assert (orin_user_dir() / "keybindings.json").read_text(encoding="utf-8") == text


def test_trusted_folders_are_listed_and_revoked(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/v1/conversations/c1/workspace-trust", headers={**AUTH, "Idempotency-Key": "t1"})
    folders = client.get("/v1/settings/trusted-folders", headers=AUTH).json()["folders"]
    assert len(folders) == 1
    revoked = client.post("/v1/settings/trusted-folders/revoke", headers=AUTH, json={"path": folders[0]})
    assert revoked.status_code == 200 and revoked.json()["folders"] == []
    assert client.post("/v1/settings/trusted-folders/revoke", headers=AUTH, json={"path": folders[0]}).status_code == 404


def test_a_shared_server_has_no_settings_routes(tmp_path: Path) -> None:
    client = _client(tmp_path, RuntimeMode.SERVER)
    assert client.get("/v1/settings/effective", headers=AUTH).status_code == 404
    assert client.put("/v1/settings/keybindings", headers=AUTH, json={"text": "[]"}).status_code == 404


@pytest.mark.parametrize("method, path, body", [
    ("put", "/v1/settings/permissions", {"layer": "user", "kind": "allow", "rule": "Read", "op": "add"}),
    ("put", "/v1/settings/keybindings", {"text": "[]"}),
    ("put", "/v1/settings/hooks", {"layer": "user", "hooks": {}}),
])
def test_orin_codes_token_cannot_change_settings(tmp_path: Path, method: str, path: str, body: dict) -> None:
    client = _client(tmp_path)
    assert client.get("/v1/settings/effective", headers=CLI).status_code == 200
    assert getattr(client, method)(path, headers=CLI, json=body).status_code == 403
