"""W5: skills and slash commands read from disk in Claude Code's format."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.settings.paths import claude_user_dir, orin_managed_dir, orin_user_dir
from agentos.skills.disk import (
    CATALOG_BUDGET_CHARS, MAX_DISK_SKILLS, load_disk_skills, parse_disk_skill, split_frontmatter, substitute,
)


def _skill(root: Path, name: str, text: str) -> Path:
    folder = root / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_bytes(text.encode("utf-8"))
    return path


CLAUDE_STYLE = """---
name: deploy-check
description: Checks a deployment before it ships
argument-hint: "[environment]"
allowed-tools: Bash(git status:*), Read
disable-model-invocation: false
user-invocable: true
model: sonnet
context: fork
agent: Explore
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: echo hi
license: MIT
---
Check $ARGUMENTS before shipping.
"""


def test_claude_code_frontmatter_is_understood_and_unknown_keys_ignored(tmp_path: Path) -> None:
    path = _skill(tmp_path, "deploy-check", CLAUDE_STYLE)
    skill = parse_disk_skill(path, name="deploy-check", origin="user", kind="skill")
    assert skill.name == "deploy-check" and skill.description == "Checks a deployment before it ships"
    assert skill.argument_hint == "[environment]"
    assert skill.allowed_tools == ("Bash(git status:*)", "Read")
    assert skill.model_invocable and skill.user_invocable
    assert skill.model == "sonnet" and skill.context == "fork" and skill.agent == "Explore"
    assert isinstance(skill.hooks, dict) and "PreToolUse" in skill.hooks
    assert skill.body == "Check $ARGUMENTS before shipping."


def test_sources_precedence_and_trust(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _skill(orin_managed_dir(), "lint", "---\ndescription: managed lint\n---\nmanaged")
    _skill(orin_user_dir(), "lint", "---\ndescription: user lint\n---\nuser")
    _skill(claude_user_dir(), "review", "---\ndescription: claude user review\n---\nreview")
    _skill(workspace / ".claude", "review", "---\ndescription: project review\n---\nproject")
    _skill(workspace / ".orin", "build", "---\ndescription: project build\n---\nbuild")
    untrusted = load_disk_skills(workspace_root=workspace, trusted=False)
    assert set(untrusted.skills) == {"lint", "review"}  # a repository's skills wait for trust
    trusted = load_disk_skills(workspace_root=workspace, trusted=True)
    assert set(trusted.skills) == {"lint", "review", "build"}
    assert trusted.get("lint").origin == "managed" and trusted.get("review").origin == "user"
    assert {name for name, _, _ in trusted.shadowed} == {"lint", "review"}


def test_commands_are_named_by_their_path(tmp_path: Path) -> None:
    commands = orin_user_dir() / "commands"
    (commands / "git").mkdir(parents=True)
    (commands / "ship.md").write_bytes(b"---\ndescription: Ship it\nargument-hint: <version>\n---\nShip version $0 now.")
    (commands / "git" / "sync.md").write_bytes(b"Sync with the remote.\n")
    catalog = load_disk_skills(workspace_root=None, trusted=False)
    assert catalog.get("ship").kind == "command" and catalog.get("ship").argument_hint == "<version>"
    assert catalog.get("/git:sync").description == "Sync with the remote."


def test_broken_and_oversized_files_are_reported_not_fatal(tmp_path: Path) -> None:
    _skill(orin_user_dir(), "broken", "---\ndescription: [unclosed\n---\nx")
    _skill(orin_user_dir(), "Bad Name", "---\ndescription: d\n---\nx")
    _skill(orin_user_dir(), "huge", "---\ndescription: d\n---\n" + "x" * 300_000)
    _skill(orin_user_dir(), "fine", "---\ndescription: d\n---\nx")
    catalog = load_disk_skills(workspace_root=None, trusted=False)
    assert set(catalog.skills) == {"fine"}
    assert len(catalog.errors) == 3


def test_the_catalog_lists_model_invocable_skills_within_a_budget(tmp_path: Path) -> None:
    _skill(orin_user_dir(), "hidden", "---\ndescription: only for people\ndisable-model-invocation: true\n---\nx")
    for index in range(80):
        _skill(orin_user_dir(), f"skill-{index:02d}", "---\ndescription: " + "d" * 400 + "\n---\nx")
    catalog = load_disk_skills(workspace_root=None, trusted=False)
    items = catalog.catalog_items()
    assert "hidden" not in {item.name for item in items}
    assert 0 < len(items) < 80
    assert sum(len(item.name) * 2 + len(item.description) + 8 for item in items) <= CATALOG_BUDGET_CHARS
    assert all(len(item.description) <= 250 for item in items)


def test_at_most_the_cap_is_loaded(tmp_path: Path) -> None:
    for index in range(MAX_DISK_SKILLS + 5):
        _skill(orin_user_dir(), f"s{index:03d}", "---\ndescription: d\n---\nx")
    catalog = load_disk_skills(workspace_root=None, trusted=False)
    assert len(catalog.skills) == MAX_DISK_SKILLS and catalog.errors


def test_substitutions(tmp_path: Path) -> None:
    text, used = substitute(
        "all=$ARGUMENTS first=$ARGUMENTS[0] second=$1 dir=${CLAUDE_SKILL_DIR} id=${ORIN_SESSION_ID}",
        arguments='prod "eu west"', session_id="chat-1", skill_dir=tmp_path,
    )
    assert used
    assert text == f'all=prod "eu west" first=prod second=eu west dir={tmp_path.as_posix()} id=chat-1'
    untouched, used = substitute("awk '{print $1}'", arguments="", session_id="", skill_dir=tmp_path)
    assert untouched == "awk '{print $1}'" and not used


@pytest.mark.parametrize("text", ["no frontmatter at all", "---\n---\nbody"])
def test_frontmatter_may_be_absent_or_empty(text: str) -> None:
    meta, body = split_frontmatter(text)
    assert meta == {} and "body" in body or body == text
