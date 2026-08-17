import json
from agentos.plugins.inspector import inspect_plugin_package


def _manifest(tmp_path, payload):
    (tmp_path / ".claude-plugin").mkdir(exist_ok=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def test_inspector_contributes_mcp_servers_declared_inline_in_the_manifest(tmp_path):
    _manifest(tmp_path, {
        "name": "obsidian-second-brain", "version": "0.14.0",
        "mcpServers": {"vault": {"command": "uv", "args": ["run", "server.py"]}},
    })

    result = inspect_plugin_package(tmp_path, package_digest="abc")

    assert [item.slug for item in result.mcp_servers] == ["obsidian-second-brain-vault"]
    assert result.contribution_count == 1


def test_mcp_json_wins_a_slug_collision_with_the_manifest(tmp_path):
    _manifest(tmp_path, {
        "name": "demo", "version": "1.0.0",
        "mcpServers": {"vault": {"command": "from-manifest"}},
    })
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"vault": {"command": "from-mcp-json"}}}), encoding="utf-8"
    )

    result = inspect_plugin_package(tmp_path, package_digest="abc")

    assert len(result.mcp_servers) == 1
    assert result.mcp_servers[0].command == "from-mcp-json"


def test_a_commands_only_plugin_is_inspectable_and_installable(tmp_path):
    _manifest(tmp_path, {"name": "demo", "version": "1.0.0", "commands": "./commands/"})
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "daily.md").write_text(
        "---\ndescription: Daily note\n---\n\nbody", encoding="utf-8"
    )

    result = inspect_plugin_package(tmp_path, package_digest="abc")

    assert [item.command_id for item in result.commands] == ["demo:daily"]
    assert result.is_installable
    assert not any("comandos declarados não são executados" in w for w in result.warnings)


def test_a_hooks_only_plugin_is_installable(tmp_path):
    _manifest(tmp_path, {"name": "demo", "version": "1.0.0"})
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": "", "hooks": [{"type": "command", "command": "python3 x.py"}]}
    ]}}), encoding="utf-8")

    result = inspect_plugin_package(tmp_path, package_digest="abc")

    assert [item.hook_id for item in result.hooks] == ["demo:SessionStart:0"]
    assert result.is_installable
    assert not any("hooks não são suportados" in warning for warning in result.warnings)


def test_inspector_reports_declarative_contributions_and_warnings(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":"demo","version":"1.0.0"}), encoding="utf-8")
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    (tmp_path / "hooks").mkdir()
    result = inspect_plugin_package(tmp_path, package_digest="abc")
    assert result.skills[0].skill_id == "demo:s"


def test_plugin_skill_id_is_namespaced_by_normalized_plugin_and_skill_names(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "Demo Tools", "version": "1.0.0"}), encoding="utf-8")
    (tmp_path / "skills" / "custom").mkdir(parents=True)
    (tmp_path / "skills" / "custom" / "SKILL.md").write_text(
        "---\nid: legacy-id\nname: Deploy Safely\nversion: 1.0.0\ndescription: d\n---\n\nbody",
        encoding="utf-8",
    )

    result = inspect_plugin_package(tmp_path, package_digest="abc")

    assert result.skills[0].skill_id == "demo-tools:deploy-safely"


# Real SKILL.md content from github.com/obra/superpowers, skills/brainstorming/SKILL.md
# at tag 5.1.0. No `version` field: real-world plugin skills declare only name/description,
# with version tracked once at the plugin level (plugin.json), not per skill.
REAL_SUPERPOWERS_BRAINSTORMING_SKILL = """---
name: brainstorming
description: "You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation."
---

# Brainstorming Ideas Into Designs

Help turn ideas into fully formed designs and specs through natural collaborative dialogue.
"""


def test_inspector_accepts_real_world_plugin_skill_without_version_field(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "superpowers", "version": "5.1.0"}), encoding="utf-8"
    )
    (tmp_path / "skills" / "brainstorming").mkdir(parents=True)
    (tmp_path / "skills" / "brainstorming" / "SKILL.md").write_text(
        REAL_SUPERPOWERS_BRAINSTORMING_SKILL, encoding="utf-8"
    )
    result = inspect_plugin_package(tmp_path, package_digest="abc")
    assert not any("quebrada" in warning.lower() for warning in result.warnings)
    assert result.skills[0].skill_id == "superpowers:brainstorming"
    assert result.skills[0].name == "brainstorming"
