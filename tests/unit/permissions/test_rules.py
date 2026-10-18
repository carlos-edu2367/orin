from __future__ import annotations

from pathlib import Path

import pytest

from agentos.permissions.rules import (
    MatchContext,
    command_matches,
    parse_rule,
    rule_applies_to_tool,
    rule_matches,
    suggest_rule,
)


@pytest.fixture
def ctx(tmp_path: Path) -> MatchContext:
    root = tmp_path / "ws"
    root.mkdir()
    return MatchContext(workspace_root=root, home=tmp_path / "home", shell="posix")


def test_parse_public_internal_and_legacy_names() -> None:
    assert parse_rule("Bash(git status)", kind="allow", source="user").tool == "run_command"
    assert parse_rule("run_command(git status)", kind="allow", source="user").tool == "run_command"
    assert parse_rule("Edit", kind="allow", source="user").specifier is None
    assert parse_rule("Edit(*)", kind="allow", source="user").specifier is None
    assert parse_rule("Grep", kind="deny", source="user").tool == "search_files"
    assert parse_rule("grep_files", kind="deny", source="user").tool == "search_files"
    assert parse_rule("mcp__github__list_issues", kind="allow", source="user").tool == "mcp__github__list_issues"
    assert parse_rule("*", kind="deny", source="managed").tool == "*"
    assert parse_rule("", kind="allow", source="user") is None
    assert parse_rule("Bash(unclosed", kind="allow", source="user") is None


def test_tool_families() -> None:
    edit = parse_rule("Edit(src/**)", kind="allow", source="user")
    assert rule_applies_to_tool(edit, "edit_file") and rule_applies_to_tool(edit, "write_file") and rule_applies_to_tool(edit, "notebook_edit")
    assert not rule_applies_to_tool(edit, "read_file")
    read = parse_rule("Read(.env)", kind="deny", source="user")
    for tool in ("read_file", "view_file", "glob_files", "search_files", "list_files"):
        assert rule_applies_to_tool(read, tool)
    write = parse_rule("Write", kind="ask", source="user")
    assert rule_applies_to_tool(write, "write_file") and not rule_applies_to_tool(write, "edit_file")
    server = parse_rule("mcp__github", kind="allow", source="user")
    assert rule_applies_to_tool(server, "mcp__github__list_issues")
    assert not rule_applies_to_tool(server, "mcp__githubx__list_issues")
    star = parse_rule("mcp__github__*", kind="allow", source="user")
    assert rule_applies_to_tool(star, "mcp__github__x") and not rule_applies_to_tool(star, "mcp__gitlab__x")


@pytest.mark.parametrize(
    ("spec", "sub", "expected"),
    [
        ("git status", "git status", True),
        ("git status", "git status --short", False),
        ("git:*", "git log -1", True),
        ("git:*", "git", True),
        ("git:*", "gitx", False),
        ("npm run *", "npm run build", True),
        ("npm run *", "npm run", True),
        ("npm test*", "npm test -- --run", True),
        (None, "anything", True),
    ],
)
def test_command_matches(spec, sub, expected) -> None:
    assert command_matches(spec, sub) is expected


def test_bash_deny_matches_any_subcommand(ctx: MatchContext) -> None:
    deny = parse_rule("Bash(rm:*)", kind="deny", source="user")
    assert rule_matches(deny, "run_command", {"command": "git status && rm -rf build"}, ctx)
    assert not rule_matches(deny, "run_command", {"command": "git status"}, ctx)


def test_path_rules(ctx: MatchContext) -> None:
    deny = parse_rule("Read(.env)", kind="deny", source="user")
    assert rule_matches(deny, "read_file", {"path": "config/.env"}, ctx)
    assert rule_matches(deny, "read_file", {"file_path": str(ctx.workspace_root / ".env")}, ctx)
    assert not rule_matches(deny, "read_file", {"path": "config/.env.example"}, ctx)
    edit = parse_rule("Edit(src/**)", kind="allow", source="user")
    assert rule_matches(edit, "notebook_edit", {"notebook_path": "src/a.ipynb"}, ctx)
    grep = parse_rule("Read(secrets/**)", kind="deny", source="user")
    assert rule_matches(grep, "search_files", {"pattern": "x", "path": "secrets"}, ctx)


def test_webfetch_agent_skill_and_mcp_specifiers(ctx: MatchContext) -> None:
    fetch = parse_rule("WebFetch(domain:example.com)", kind="allow", source="user")
    assert rule_matches(fetch, "fetch_url", {"url": "https://example.com/a"}, ctx)
    assert rule_matches(fetch, "fetch_url", {"url": "https://docs.example.com/a"}, ctx)
    assert not rule_matches(fetch, "fetch_url", {"url": "https://example.com.evil.io/"}, ctx)
    assert not rule_matches(fetch, "fetch_url", {"url": "https://notexample.com/"}, ctx)
    agent = parse_rule("Agent(Explore)", kind="allow", source="user")
    assert rule_matches(agent, "run_subagent", {"subagent_type": "explore"}, ctx)
    assert not rule_matches(agent, "run_subagent", {"subagent_type": "general"}, ctx)
    skill = parse_rule("Skill(pdf:*)", kind="allow", source="user")
    assert rule_matches(skill, "use_skill", {"skill_id": "pdf:fill"}, ctx)
    exact = parse_rule("Skill(pdf)", kind="allow", source="user")
    assert rule_matches(exact, "use_skill", {"skill_id": "pdf"}, ctx) and not rule_matches(exact, "use_skill", {"skill_id": "pdfx"}, ctx)


def test_suggestions_use_public_syntax(ctx: MatchContext) -> None:
    assert suggest_rule("run_command", {"command": "npm test -- --run"}, ctx) == "Bash(npm test:*)"
    assert suggest_rule("run_command", {"command": "ls -la"}, ctx) == "Bash(ls:*)"
    assert suggest_rule("run_command", {"command": "git status && rm x"}, ctx) == "Bash(git status:*)"
    assert suggest_rule("edit_file", {"path": "src/app/main.py"}, ctx) == "Edit(src/**)"
    assert suggest_rule("write_file", {"path": "notes.md"}, ctx) == "Edit(*.md)"
    assert suggest_rule("fetch_url", {"url": "https://docs.python.org/3/"}, ctx) == "WebFetch(domain:docs.python.org)"
    assert suggest_rule("mcp__github__list_issues", {}, ctx) == "mcp__github__list_issues"


POSIX_RM_VARIANTS = (
    "/bin/rm -rf x",
    "./rm -rf x",
    "\\rm -rf x",
    '"rm" -rf x',
    "'rm' -rf x",
    "r''m -rf x",
    "command rm -rf x",
    "builtin rm -rf x",
    "exec rm -rf x",
    "env rm -rf x",
    "env FOO=1 rm -rf x",
    "sudo rm -rf x",
    "sudo -u root rm -rf x",
    "nice -n 5 rm -rf x",
    "xargs rm -rf",
    "command env FOO=1 /bin/rm -rf x",
)


@pytest.mark.parametrize("command", POSIX_RM_VARIANTS)
def test_deny_rule_sees_through_path_quotes_and_wrappers(ctx: MatchContext, command: str) -> None:
    deny = parse_rule("Bash(rm:*)", kind="deny", source="user")
    assert rule_matches(deny, "run_command", {"command": command}, ctx), command


@pytest.fixture
def cmd_ctx(tmp_path: Path) -> MatchContext:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return MatchContext(workspace_root=root, home=tmp_path / "home", shell="cmd")


@pytest.mark.parametrize(
    ("spec", "command"),
    [
        ("del:*", "DEL x"),
        ("del:*", "del.exe x"),
        ("del:*", "C:\Windows\System32\del.exe x"),
        ("del:*", '"del" x'),
        ("git push:*", "GIT push origin main"),
        ("git push:*", "git.exe push"),
        ("git push:*", '"git" push'),
        ("git push:*", "git.cmd push"),
    ],
)
def test_deny_rule_is_case_and_extension_insensitive_on_cmd(cmd_ctx: MatchContext, spec: str, command: str) -> None:
    deny = parse_rule(f"Bash({spec})", kind="deny", source="user")
    assert rule_matches(deny, "run_command", {"command": command}, cmd_ctx), command


@pytest.mark.parametrize(
    ("spec", "sub"),
    [
        ("git:*", "/tmp/evil/git status"),
        ("git:*", "./git status"),
        ("git:*", "sudo git status"),
        ("git:*", 'env -S "git status; rm -rf /"'),
    ],
)
def test_allow_matching_never_widens_through_paths_or_privilege_wrappers(spec: str, sub: str) -> None:
    from agentos.permissions.rules import command_allowed

    assert command_allowed(spec, sub, shell="posix") is False, sub


@pytest.mark.parametrize("sub", ['"git" status', "command git status", "env FOO=1 git status"])
def test_allow_matching_accepts_exact_normalized_forms(sub: str) -> None:
    from agentos.permissions.rules import command_allowed

    assert command_allowed("git:*", sub, shell="posix") is True


def test_cmd_allow_matching_is_case_insensitive_posix_is_not() -> None:
    from agentos.permissions.rules import command_allowed

    assert command_allowed("git:*", "GIT status", shell="cmd") is True
    assert command_allowed("git:*", "GIT status", shell="posix") is False
