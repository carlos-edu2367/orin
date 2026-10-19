"""W5: subagent definitions read from disk in Claude Code's format."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.agent_definitions import (
    MAX_AGENT_DEFINITIONS, AgentDefinition, AgentToolPolicy, child_permission_mode, load_agent_definitions, parse_agent_file,
    resolve_agent_model,
)
from agentos.settings.paths import claude_user_dir, orin_managed_dir, orin_user_dir


def _agent(root: Path, file: str, text: str) -> Path:
    folder = root / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file
    path.write_bytes(text.encode("utf-8"))
    return path


REVIEWER = """---
name: code-reviewer
description: Reviews a diff for bugs and risky changes
tools: Read, Grep, Glob, Bash
disallowedTools: [Bash]
model: haiku
permissionMode: acceptEdits
maxTurns: 12
skills: [checklist]
background: true
omitClaudeMd: true
isolation: worktree
effort: high
color: blue
memory: project
hooks:
  Stop:
    - hooks:
        - type: command
          command: echo done
---
You review code. Report findings as path:line.
"""


def test_claude_code_frontmatter_is_understood(tmp_path: Path) -> None:
    definition = parse_agent_file(_agent(tmp_path, "reviewer.md", REVIEWER), origin="user")
    assert definition.name == "code-reviewer" and definition.description.startswith("Reviews a diff")
    assert definition.tools == ("Read", "Grep", "Glob", "Bash") and definition.disallowed_tools == ("Bash",)
    assert definition.model == "haiku" and definition.permission_mode == "acceptEdits" and definition.max_turns == 12
    assert definition.skills == ("checklist",) and definition.background and definition.omit_claude_md
    assert definition.isolation == "worktree" and definition.effort == "high" and definition.memory == "project"
    assert isinstance(definition.hooks, dict) and definition.prompt == "You review code. Report findings as path:line."


@pytest.mark.parametrize("text", [
    "---\ndescription: no name\n---\nx",
    "---\nname: Has Spaces\ndescription: d\n---\nx",
    "---\nname: no-description\n---\nx",
    "no frontmatter",
])
def test_invalid_definitions_are_reported(tmp_path: Path, text: str) -> None:
    _agent(orin_user_dir(), "bad.md", text)
    catalog = load_agent_definitions(workspace_root=None, trusted=False)
    assert catalog.custom() == [] and catalog.errors


def test_precedence_trust_and_the_verifier_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _agent(orin_managed_dir(), "a.md", "---\nname: shared\ndescription: managed\n---\nm")
    _agent(workspace / ".claude", "a.md", "---\nname: shared\ndescription: project\n---\np")
    _agent(workspace / ".claude", "b.md", "---\nname: helper\ndescription: project helper\n---\np")
    _agent(claude_user_dir(), "b.md", "---\nname: helper\ndescription: user helper\n---\nu")
    untrusted = load_agent_definitions(workspace_root=workspace, trusted=False)
    assert untrusted.get("helper").description == "user helper"
    trusted = load_agent_definitions(workspace_root=workspace, trusted=True)
    assert trusted.get("shared").origin == "managed" and trusted.get("helper").description == "project helper"
    assert trusted.get("verifier").origin == "builtin" and "Edit" not in (trusted.get("verifier").tools or ())


def test_a_definition_may_replace_a_builtin_name(tmp_path: Path) -> None:
    _agent(orin_user_dir(), "explore.md", "---\nname: explore\ndescription: my explorer\n---\nx")
    _agent(orin_user_dir(), "gp.md", "---\nname: general-purpose\ndescription: my general\n---\nx")
    catalog = load_agent_definitions(workspace_root=None, trusted=False)
    assert catalog.get("Explore").description == "my explorer"
    assert catalog.get("general").description == "my general"


def test_at_most_the_cap_is_loaded(tmp_path: Path) -> None:
    for index in range(MAX_AGENT_DEFINITIONS + 3):
        _agent(orin_user_dir(), f"a{index:03d}.md", f"---\nname: a{index:03d}\ndescription: d\n---\nx")
    catalog = load_agent_definitions(workspace_root=None, trusted=False)
    assert len(catalog.agents) == MAX_AGENT_DEFINITIONS + 1  # the cap of files, plus the verifier builtin
    assert catalog.errors


@pytest.mark.parametrize(("parent", "requested", "expected"), [
    ("ask_permissions", "acceptEdits", "ask_permissions"),   # never more permissive than the parent
    ("accept_edits", "plan", "plan_mode"),                     # narrower is fine
    ("accept_edits", "default", "ask_permissions"),
    ("plan_mode", "bypassPermissions", "plan_mode"),
    ("full_autonomy", "acceptEdits", "accept_edits"),          # a bypassing parent may run any child mode
    ("accept_edits", None, "accept_edits"),
    ("ask_permissions", "dontAsk", "dont_ask"),
])
def test_permission_modes_never_escalate(parent: str, requested: str | None, expected: str) -> None:
    assert child_permission_mode(parent, requested) == expected


def test_model_aliases() -> None:
    aliases = {"haiku": "anthropic/claude-haiku-5-5", "Sonnet": "anthropic/claude-sonnet-5-5"}
    assert resolve_agent_model("haiku", aliases) == "anthropic/claude-haiku-5-5"
    assert resolve_agent_model("sonnet", aliases) == "anthropic/claude-sonnet-5-5"
    assert resolve_agent_model("opus", aliases) is None  # an alias with no mapping inherits
    assert resolve_agent_model("inherit", aliases) is None and resolve_agent_model(None, aliases) is None
    assert resolve_agent_model("openrouter/some-model", {}) == "openrouter/some-model"


def test_the_tool_policy_narrows_and_never_widens() -> None:
    class _Base:
        def allows(self, name, tags):
            return name != "write_file"

    definition = AgentDefinition(
        name="x", description="d", prompt="", origin="user", tools=("Read", "Write", "Bash", "mcp__github"),
        disallowed_tools=("Bash",),
    )
    policy = AgentToolPolicy(_Base(), definition)
    assert policy.allows("read_file", ()) and not policy.allows("write_file", ())  # the base policy still decides
    assert not policy.allows("run_command", ()) and not policy.allows("edit_file", ())
    assert policy.allows("mcp__github__create_issue", ()) and not policy.allows("mcp__slack__post", ())
    everything = AgentToolPolicy(None, AgentDefinition(name="y", description="d", prompt="", origin="user"))
    assert everything.allows("edit_file", ())
