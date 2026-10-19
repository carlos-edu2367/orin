"""W5 review fixes (part 1): regression tests for the findings in docs/superpowers/reviews/2026-10-19-w5-review.md."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.permissions.engine import PermissionEngine
from agentos.settings import load_effective_settings
from agentos.skills.disk import load_disk_skills


def _project_skill(tmp_path: Path, allowed: str) -> tuple[Path, AgentToolset]:
    root = tmp_path / "w"
    folder = root / ".claude" / "skills" / "wide"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(f"---\ndescription: Wide\nallowed-tools: {allowed}\n---\nx".encode("utf-8"))
    toolset = AgentToolset(ConversationWorkspace.at_root(root), public_tool_names=True)
    toolset._disk_skills = load_disk_skills(workspace_root=root, trusted=True)
    assert toolset.invoke("Skill", {"skill": "wide"}).status == "succeeded"
    return root, toolset


# W5-R2: a repository's skill may pre-approve scoped shell commands, never every command.


def test_w5_r2_a_project_skill_drops_wildcard_shell_grants(tmp_path: Path) -> None:
    root, toolset = _project_skill(tmp_path, '["Bash(:*)", "Bash(**)", "PowerShell(:*)", "*(:*)", "Bash(git status:*)", "Read"]')
    grants = toolset.skill_grants()
    assert {rule.raw for rule in grants} == {"Bash(git status:*)", "Read"}
    engine = PermissionEngine(root, load_effective_settings(root, trusted=True), shell="cmd", home=tmp_path / "home")
    for command in ("curl https://example.invalid/x -o x.exe", r"del /s /q C:\Users\someone\Documents", "git push --force"):
        assert engine.decide("run_command", {"command": command}, "ask_permissions", extra_allow=grants).behavior == "ask", command
    assert engine.decide("run_command", {"command": "git status"}, "ask_permissions", extra_allow=grants).behavior == "allow"
    assert engine.decide("powershell", {"command": r"Remove-Item -Recurse C:\temp\x"}, "ask_permissions", extra_allow=grants).behavior == "ask"


def test_w5_r2_a_project_skill_drops_a_shell_or_wrapper_grant_that_runs_any_command(tmp_path: Path) -> None:
    _, toolset = _project_skill(tmp_path, '["Bash(env:*)", "Bash(sh:*)", "Bash(/usr/bin/env:*)", "Bash(cmd.exe:*)", "Bash(git:*)"]')
    assert {rule.raw for rule in toolset.skill_grants()} == {"Bash(git:*)"}
