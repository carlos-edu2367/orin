import json

from agentos.plugins.inspector import inspect_plugin_package

MANIFEST = {
    "name": "obsidian-second-brain",
    "version": "0.14.0",
    "description": "Turns your Obsidian vault into memory Claude can search.",
    "author": {"name": "Eugeniu Ghelbur"},
    "homepage": "https://github.com/eugeniughelbur/obsidian-second-brain",
    "commands": "./commands/",
    "mcpServers": {"vault": {"command": "uv", "args": ["run", "--with", "mcp<2", "python", "${CLAUDE_PLUGIN_ROOT}/integrations/obsidian-mcp-server/server.py"]}},
}

HOOKS = {"hooks": {
    "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/load_vault_context.py"'}]}],
    "PostToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|create_file", "hooks": [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/validate-ai-first.sh"', "timeout": 10}]}],
    "PostCompact": [{"matcher": "", "hooks": [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/obsidian-bg-agent.sh"', "timeout": 10, "async": True}]}],
}}


def test_the_reference_plugin_installs_with_every_kind_it_declares(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (tmp_path / "commands").mkdir()
    for name in ("obsidian-daily", "obsidian-capture", "research"):
        (tmp_path / "commands" / f"{name}.md").write_text(
            f"---\ndescription: {name}\ncategory: vault\n---\n\nbody for {name}", encoding="utf-8"
        )
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")
    # A top-level SKILL.md, as this repository actually has: outside skills/*/,
    # so it is correctly not a skill contribution.
    (tmp_path / "SKILL.md").write_text("---\nname: x\ndescription: d\n---\n\nbody", encoding="utf-8")

    result = inspect_plugin_package(tmp_path, package_digest="abc")

    assert result.is_installable
    assert result.skills == ()
    assert [item.slug for item in result.mcp_servers] == ["obsidian-second-brain-vault"]
    assert len(result.commands) == 3
    assert len(result.hooks) == 3
    assert result.contribution_count == 7
