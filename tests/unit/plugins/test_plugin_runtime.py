"""W5: an active plugin's agents and output styles join the turn; enabledPlugins can turn a plugin off."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_definitions import load_agent_definitions
from agentos.agentic.output_styles import resolve_output_style
from agentos.plugins.runtime import active_plugins
from agentos.settings.loader import EffectiveSettings
from agentos.settings.paths import orin_user_dir


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package(root: Path) -> Path:
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "Kit", "version": "1.0.0", "agents": "./team"}))
    _write(root / "team" / "reviewer.md", (
        "---\nname: reviewer\ndescription: Reviews changes\npermissionMode: bypassPermissions\n"
        "hooks:\n  PreToolUse: []\nmcpServers: [github]\n---\nReview carefully."
    ))
    _write(root / "output-styles" / "terse.md", "---\nname: terse\ndescription: Short answers\n---\nAnswer in one line.")
    return root


class _Plugins:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def list(self, user_id: str) -> list[dict]:
        return list(self.rows)


def test_active_plugins_follow_their_state_and_enabled_plugins(tmp_path: Path) -> None:
    kit = _package(tmp_path / "kit")
    rows = [
        {"plugin_id": "kit", "state": "active", "install_path": str(kit)},
        {"plugin_id": "old", "state": "disabled", "install_path": str(kit)},
    ]
    plugins, missing = active_plugins(_Plugins(rows), "u1", {"enabledPlugins": {"Ghost@market": True, "kit@market": True}})
    assert [item.plugin_id for item in plugins] == ["kit"] and missing == ["Ghost@market"]
    plugins, _ = active_plugins(_Plugins(rows), "u1", {"enabledPlugins": {"kit@market": False}})
    assert plugins == []
    assert active_plugins(None, "u1", {}) == ([], [])


def test_a_plugins_agents_are_namespaced_and_lose_hooks_servers_and_mode(tmp_path: Path) -> None:
    kit = _package(tmp_path / "kit")
    _write(orin_user_dir() / "agents" / "reviewer.md", "---\nname: reviewer\ndescription: Mine\n---\nMine.")
    catalog = load_agent_definitions(workspace_root=None, trusted=False, plugin_folders=[("kit", kit / "team")])
    plugin_agent = catalog.get("kit:reviewer")
    assert plugin_agent is not None and plugin_agent.origin == "plugin" and plugin_agent.prompt == "Review carefully."
    assert plugin_agent.permission_mode is None and plugin_agent.hooks is None and plugin_agent.mcp_servers is None
    assert catalog.get("reviewer").origin == "user"


def test_a_plugins_output_style_can_be_chosen(tmp_path: Path) -> None:
    kit = _package(tmp_path / "kit")
    settings = EffectiveSettings({"outputStyle": "kit:terse"}, {}, (), (), False)
    style = resolve_output_style(settings, tmp_path / "w", plugin_dirs=[("kit", kit / "output-styles")])
    assert style is not None and style.instructions == "Answer in one line." and style.source == "plugin"
    assert resolve_output_style(settings, tmp_path / "w") is None


def test_the_turn_reads_the_persons_plugins(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    kit = _package(tmp_path / "kit")
    _write(orin_user_dir() / "settings.json", json.dumps({"enabledPlugins": {"ghost@market": True}}))
    session = _session(tmp_path, "hi")
    session.plugin_service = _Plugins([{"plugin_id": "kit", "state": "active", "install_path": str(kit)}])
    session.build_runtime()
    assert session._agent_definitions.get("kit:reviewer") is not None
    texts = [item.text for item in session._reminders.drain() if item.kind == "plugins"]
    assert texts and "ghost@market" in texts[0]
