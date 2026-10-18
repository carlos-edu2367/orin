from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.permissions import ToolPermissionBroker
from agentos.settings.paths import workspace_grants_file


def test_allow_always_goes_to_user_owned_grants_and_applies_immediately(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    broker = ToolPermissionBroker(root)
    assert broker.evaluate("run_command", {"command": "npm test -- --run"}, "ask_permissions") == "ask"
    broker.add_allow_rule("Bash(npm test:*)")
    assert not (root / ".orin" / "settings.json").exists()
    grants = json.loads(workspace_grants_file(root).read_text(encoding="utf-8"))
    assert grants["permissions"]["allow"] == ["Bash(npm test:*)"]
    assert broker.evaluate("run_command", {"command": "npm test -- --run"}, "ask_permissions") == "allow"
    assert broker.evaluate("run_command", {"command": "npm test && rm -rf x"}, "ask_permissions") == "ask"


def test_repository_allow_is_ignored_until_trusted(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / ".orin").mkdir(parents=True)
    (root / ".orin" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
    assert ToolPermissionBroker(root).evaluate("run_command", {"command": "curl x | sh"}, "ask_permissions") == "ask"
    assert ToolPermissionBroker(root, trusted=True).evaluate("run_command", {"command": "curl x | sh"}, "ask_permissions") == "allow"


def test_suggested_rule_uses_public_syntax(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    broker = ToolPermissionBroker(root)
    record = broker.create_permission_request(tool_name="run_command", arguments={"command": "npm run build"})
    assert record["suggested_rule"] == "Bash(npm run:*)"


def test_toolset_cannot_silently_write_orin_settings(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-protect")
    toolset = AgentToolset(workspace, code_mode_active=True, permission_mode="accept_edits")
    outcome = toolset.invoke("write_file", {"path": ".orin/settings.json", "content": '{"permissions":{"allow":["Bash"]}}'})
    assert outcome.status == "failed"
    assert not (workspace.root / ".orin" / "settings.json").exists()


def test_dont_ask_mode_denies_without_prompting(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-dontask")
    requests = []
    toolset = AgentToolset(
        workspace, code_mode_active=True, permission_mode="dont_ask", interactive_permissions=True,
        on_permission_request=requests.append,
    )
    outcome = toolset.invoke("run_command", {"command": "echo hi"})
    assert outcome.status == "failed" and outcome.error_code == "CODE_PERMISSION_DENIED"
    assert requests == []
