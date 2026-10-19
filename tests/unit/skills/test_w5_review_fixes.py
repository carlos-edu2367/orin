"""W5 review fixes for skill and agent frontmatter (docs/superpowers/reviews/2026-10-19-w5-review.md, W5-R11).

YAML aliases share one structure among many references and expand when a reader walks them: a few hundred bytes of
frontmatter can become thousands of tool entries. Frontmatter with an anchor or an alias is refused, and frontmatter
is capped at 64 KB before it is parsed. The bomb below has 4 levels (9 ** 4 = 6,561 entries once expanded).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.agent_definitions import parse_agent_file
from agentos.plugins.inspector import inspect_plugin_package
from agentos.skills.disk import parse_disk_skill, split_frontmatter
from agentos.skills.models import SkillScope, SkillSource
from agentos.skills.parser import SkillParseError, parse_skill_file


def _bomb(levels: int = 4) -> str:
    lines = ['l0: &l0 ["Read","Read","Read","Read","Read","Read","Read","Read","Read"]']
    for level in range(1, levels):
        lines.append(f"l{level}: &l{level} [" + ",".join([f"*l{level - 1}"] * 9) + "]")
    return "\n".join(lines) + "\n"


def _skill_file(tmp_path: Path, frontmatter: str) -> Path:
    folder = tmp_path / "boom"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_text(f"---\n{frontmatter}---\nbody\n", encoding="utf-8")
    return path


def test_an_alias_in_a_skill_is_refused(tmp_path: Path) -> None:
    path = _skill_file(tmp_path, f"description: d\n{_bomb()}allowed-tools: [*l3]\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        parse_disk_skill(path, name="boom", origin="project", kind="skill")


def test_an_alias_in_an_agent_file_is_refused(tmp_path: Path) -> None:
    agent = tmp_path / "boom.md"
    agent.write_text(f"---\nname: boom\ndescription: d\n{_bomb()}tools: [*l3]\n---\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid YAML"):
        parse_agent_file(agent, origin="project")


def test_an_alias_in_a_plugin_skill_is_refused_by_the_lenient_reader(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(f"---\nname: boom\ndescription: d\n{_bomb()}tags: [*l3]\n---\nbody\n", encoding="utf-8")
    with pytest.raises(SkillParseError, match="not valid YAML"):
        parse_skill_file(
            skill, include_instructions=False, source=SkillSource.PLUGIN, scope=SkillScope.USER,
            required_fields=frozenset({"name", "description"}), default_version="1.0.0", lenient=True,
        )


def test_an_alias_in_an_agent_of_a_plugin_is_reported_not_expanded(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "kit", "version": "1.0.0"}), encoding="utf-8")
    (plugin / "agents").mkdir()
    (plugin / "agents" / "boom.md").write_text(
        f"---\nname: boom\ndescription: d\n{_bomb()}tools: [*l3]\n---\nbody\n", encoding="utf-8")
    inspection = inspect_plugin_package(plugin, package_digest="x")
    assert inspection.agents == ()
    assert any("boom.md" in warning for warning in inspection.warnings)


def test_an_anchor_without_an_alias_is_refused(tmp_path: Path) -> None:
    path = _skill_file(tmp_path, "description: d\nnote: &n hello\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        parse_disk_skill(path, name="boom", origin="project", kind="skill")


def test_frontmatter_larger_than_64_kb_is_refused_before_it_is_parsed() -> None:
    with pytest.raises(ValueError, match="larger than 64 KB"):
        split_frontmatter("---\nnote: " + "x" * (64 * 1024) + "\n---\nbody\n")


def test_ordinary_frontmatter_still_parses_and_so_does_a_long_one_under_the_cap() -> None:
    meta, body = split_frontmatter("---\nname: boom\nallowed-tools: [Read, Grep]\nnote: " + "x" * 1000 + "\n---\nbody\n")
    assert meta["name"] == "boom" and meta["allowed-tools"] == ["Read", "Grep"]
    assert body == "body\n"
