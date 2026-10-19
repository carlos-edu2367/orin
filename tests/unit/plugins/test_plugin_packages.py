"""W5: plugin packages laid out as Claude Code (and Orin) write them."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.plugins.activator import PluginActivator
from agentos.plugins.hook_executor import resolve_argv
from agentos.plugins.inspector import inspect_plugin_package
from agentos.plugins.manifest import ManifestRejected, manifest_file, parse_plugin_manifest
from agentos.plugins.models import McpServerContribution, PluginInspection, PluginRef
from agentos.plugins.variables import plugin_data_dir
from agentos.skills.parser import parse_skill_markdown
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_orins_manifest_folder_comes_first(tmp_path: Path) -> None:
    assert manifest_file(tmp_path) is None
    _write(tmp_path / "plugin.json", "{}")
    assert manifest_file(tmp_path) == tmp_path / "plugin.json"
    _write(tmp_path / ".claude-plugin" / "plugin.json", "{}")
    assert manifest_file(tmp_path) == tmp_path / ".claude-plugin" / "plugin.json"
    _write(tmp_path / ".orin-plugin" / "plugin.json", "{}")
    assert manifest_file(tmp_path) == tmp_path / ".orin-plugin" / "plugin.json"


def test_the_version_is_optional_and_component_paths_may_be_lists() -> None:
    manifest = parse_plugin_manifest({"name": "demo", "agents": ["./team", "more/"], "skills": "./my-skills", "outputStyles": "./styles"})
    assert manifest.version == "0.0.0"
    assert manifest.agents_paths == ("team", "more") and manifest.skills_paths == ("my-skills",) and manifest.output_styles_paths == ("styles",)
    defaults = parse_plugin_manifest({"name": "demo", "version": "1.2.3"})
    assert (defaults.agents_paths, defaults.skills_paths, defaults.output_styles_paths) == (("agents",), ("skills",), ("output-styles",))
    with pytest.raises(ManifestRejected):
        parse_plugin_manifest({"name": "demo", "agents": ["../outside"]})
    with pytest.raises(ManifestRejected):
        parse_plugin_manifest({"name": "demo", "version": "latest"})


CLAUDE_CODE_SKILL = """---
name: release-notes
description: >
  Writes release notes from the commits
  since the last tag.
allowed-tools: Bash(git log:*), Read
argument-hint: [tag]
disable-model-invocation: false
---
Write the notes.
"""


def test_a_claude_code_skill_parses_leniently() -> None:
    with pytest.raises(Exception):
        parse_skill_markdown(CLAUDE_CODE_SKILL, required_fields=frozenset({"name", "description"}), default_version="1.0.0")
    skill = parse_skill_markdown(CLAUDE_CODE_SKILL, required_fields=frozenset({"name", "description"}), default_version="1.0.0", lenient=True)
    assert skill.name == "release-notes" and skill.description.startswith("Writes release notes") and skill.instructions == "Write the notes."


def test_the_inspector_reads_orins_manifest_custom_paths_and_claude_code_skills(tmp_path: Path) -> None:
    _write(tmp_path / ".orin-plugin" / "plugin.json", json.dumps({"name": "Kit", "agents": "./team", "skills": "./my-skills"}))
    _write(tmp_path / "my-skills" / "release-notes" / "SKILL.md", CLAUDE_CODE_SKILL)
    _write(tmp_path / "team" / "reviewer.md", "---\nname: reviewer\ndescription: Reviews changes\ntools: Read, Grep\n---\nReview carefully.")
    result = inspect_plugin_package(tmp_path, package_digest="abc")
    assert [item.skill_id for item in result.skills] == ["kit:release-notes"] and not result.warnings
    assert [(item.agent_id, item.name, item.relative_path) for item in result.agents] == [("kit:reviewer", "reviewer", "team/reviewer.md")]
    assert result.ref.version == "0.0.0"


def test_activation_names_the_plugin_folder_and_its_data_folder(tmp_path: Path) -> None:
    inspection = PluginInspection(
        PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc",
        mcp_servers=(McpServerContribution("demo-db", "db", "stdio", "node", ("${ORIN_PLUGIN_ROOT}/server.js", "--data", "${CLAUDE_PLUGIN_DATA}"), None, ()),),
    )
    mcp = FakeMcpService()
    PluginActivator(skill_library=FakeSkillLibrary(), mcp_service=mcp).activate(user_id="u1", install_path=tmp_path, inspection=inspection)
    args = mcp.proposed[0]["args"]
    assert Path(args[0]).resolve() == (tmp_path / "server.js").resolve()
    assert args[2] == str(plugin_data_dir("u1", "demo")) and plugin_data_dir("u1", "demo").is_dir()
    assert plugin_data_dir("u1", "demo") != plugin_data_dir("u2", "demo")


def test_a_hook_may_name_orins_plugin_root(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "run.py", "print('ok')")
    argv = resolve_argv("python ${ORIN_PLUGIN_ROOT}/hooks/run.py --fast", install_path=tmp_path)
    assert Path(argv[1]) == (tmp_path / "hooks" / "run.py").resolve() and argv[2] == "--fast"


def test_a_frontmatter_alias_bomb_is_refused_not_expanded_into_text() -> None:
    import time

    from agentos.skills.parser import SkillParseError

    levels = ["d0: &d0 [" + ", ".join(["'" + "y" * 100 + "'"] * 10) + "]"]
    levels += [f"d{i}: &d{i} [" + ", ".join([f"*d{i - 1}"] * 10) + "]" for i in range(1, 6)]
    document = "---\nname: bomb\nversion: 1.0.0\n" + "\n".join(levels) + "\ndescription: *d5\n---\nBody\n"
    started = time.monotonic()
    with pytest.raises(SkillParseError):
        parse_skill_markdown(document, lenient=True, required_fields=frozenset({"name", "description"}))
    assert time.monotonic() - started < 5
