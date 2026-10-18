from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.permissions.paths import glob_to_regex, is_protected_path, path_rule_matches


def test_glob_to_regex_basics() -> None:
    rx = glob_to_regex("src/**/*.ts", case_insensitive=False)
    assert rx.match("src/a.ts") and rx.match("src/x/y/a.ts")
    assert not rx.match("lib/a.ts") and not rx.match("src/a.tsx")
    # DEVIATION (logged): the plan asserted None here, but its own glob_to_regex
    # maps "*" to [^/]* (gitignore semantics), and a deny/ask rule on "*.env"
    # must cover ".env" too, so the expectation is a match.
    assert glob_to_regex("*.env", case_insensitive=False).match(".env")
    assert glob_to_regex("*.env", case_insensitive=False).match("prod.env")
    assert glob_to_regex("a?c", case_insensitive=False).match("abc")
    assert glob_to_regex("[!a]bc", case_insensitive=False).match("xbc")
    assert not glob_to_regex("[!a]bc", case_insensitive=False).match("abc")
    assert glob_to_regex("A.TXT", case_insensitive=True).match("a.txt")


def _m(spec: str, target: Path, root: Path, home: Path) -> bool:
    return path_rule_matches(spec, target, workspace_root=root, home=home)


def test_specifier_anchors(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    target = root / "src" / "app" / "main.ts"
    assert _m("src/**", target, root, home)
    assert _m("/src/**/*.ts", target, root, home)
    assert _m("./src/app/main.ts", target, root, home)
    assert _m("main.ts", target, root, home)          # name at any depth
    assert _m("*.ts", target, root, home)
    assert _m("src/", target, root, home)              # trailing slash = below
    assert not _m("lib/**", target, root, home)
    assert not _m("src/*.ts", target, root, home)       # single * does not cross /
    assert _m("*", target, root, home) and _m("**", target, root, home)
    assert _m("~/.ssh/**", home / ".ssh" / "id_rsa", root, home)
    assert not _m("~/.ssh/**", root / ".ssh" / "id_rsa", root, home)
    absolute = "//" + (tmp_path / "other" / "x.txt").as_posix().lstrip("/")
    assert _m(absolute, tmp_path / "other" / "x.txt", root, home)
    # relative patterns never match outside the workspace
    assert not _m("*.txt", tmp_path / "other" / "x.txt", root, home)


def test_parent_traversal_in_target_is_resolved(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    assert not _m("src/**", root / "src" / ".." / ".." / "escape.txt", root, tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_windows_case_insensitive(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    assert _m("SRC/**", root / "src" / "a.py", root, tmp_path)


@pytest.mark.parametrize(
    "relative",
    [
        ".bashrc", "sub/.bashrc", ".npmrc", ".mcp.json", ".envrc", ".pre-commit-config.yaml",
        ".git/config", "pkg/.git/hooks/pre-commit", ".orin/settings.json", ".orin/settings.local.json",
        ".orin/mcp.json", ".orin/launch.json", ".orin/permissions/req.json", ".orin/agents/x.md",
        ".orin/skills/a/SKILL.md", ".orin/hooks/h.json", ".claude/settings.json", ".claude/agents/x.md",
        ".claude/commands/x.md", ".vscode/../.git/HEAD",
    ],
)
def test_protected(relative: str, tmp_path: Path) -> None:
    assert is_protected_path(tmp_path / relative, workspace_root=tmp_path), relative


@pytest.mark.parametrize(
    "relative",
    ["src/app.py", ".orin/plans/p.md", ".orin/worktrees/w/file.py", ".orin/tool-outputs/x.txt", "README.md", "CLAUDE.md", "ORIN.md", ".gitignore", ".github/workflows/ci.yml"],
)
def test_not_protected(relative: str, tmp_path: Path) -> None:
    assert not is_protected_path(tmp_path / relative, workspace_root=tmp_path), relative


def test_outside_workspace_is_not_reported_as_protected_here(tmp_path: Path) -> None:
    # confinement is the workspace layer's job; this function only classifies files inside
    assert not is_protected_path(tmp_path.parent / "x.txt", workspace_root=tmp_path)
