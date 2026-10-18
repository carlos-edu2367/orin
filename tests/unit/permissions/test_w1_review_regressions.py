"""Regression tests for the W1 adversarial review (R3 onward)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentos.code_mode.models import CodePermissionMode as M
from agentos.permissions.engine import PermissionEngine
from agentos.permissions.paths import is_protected_path, workspace_path
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir

WINDOWS = os.name == "nt"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


def _engine(root: Path, user: dict | None = None, shell: str = "posix") -> PermissionEngine:
    if user is not None:
        orin_user_dir().mkdir(parents=True, exist_ok=True)
        (orin_user_dir() / "settings.json").write_text(json.dumps(user), encoding="utf-8")
    settings = load_effective_settings(root, trusted=False)
    return PermissionEngine(root, settings, shell=shell, home=root.parent / "home")


# -- W1-R3: targets are normalized the way ConversationWorkspace.resolve does ----


@pytest.mark.parametrize("raw", ["/.env", "\\.env", "//.env", "./.env", ".env"])
def test_workspace_path_mirrors_the_tool_resolution(root: Path, raw: str) -> None:
    assert workspace_path(raw, root) == root / ".env"


@pytest.mark.parametrize("path", ["/.env", "\\.env", "//.env", "./.env"])
def test_deny_read_applies_to_leading_separator_paths(root: Path, path: str) -> None:
    engine = _engine(root, user={"permissions": {"deny": ["Read(.env)"]}})
    assert engine.decide("read_file", {"path": path}, M.FULL_AUTONOMY).behavior == "deny", path


@pytest.mark.skipif(not WINDOWS, reason="NTFS normalization")
def test_ntfs_trailing_dot_space_and_stream_are_protected(tmp_path: Path) -> None:
    assert is_protected_path(tmp_path / ".git " / "hooks" / "x", workspace_root=tmp_path)
    assert is_protected_path(tmp_path / ".git ./hooks/c", workspace_root=tmp_path)
    assert is_protected_path(tmp_path / ".git::$INDEX_ALLOCATION" / "hooks" / "a", workspace_root=tmp_path)
    assert is_protected_path(tmp_path / ".git" / "config:stream", workspace_root=tmp_path)


@pytest.mark.skipif(WINDOWS, reason="posix names keep trailing spaces")
def test_posix_trailing_space_is_a_different_name(tmp_path: Path) -> None:
    assert not is_protected_path(tmp_path / ".git " / "hooks" / "x", workspace_root=tmp_path)


@pytest.mark.skipif(not WINDOWS, reason="NTFS normalization")
def test_ntfs_edit_into_protected_dir_asks_for_a_human_in_accept_edits(root: Path) -> None:
    engine = _engine(root)
    decision = engine.decide("edit_file", {"path": ".git /hooks/pre-commit"}, M.ACCEPT_EDITS)
    assert decision.behavior == "ask" and decision.requires_human is True


@pytest.mark.skipif(not WINDOWS, reason="NTFS normalization")
def test_ntfs_trailing_space_env_is_denied_by_read_rule(root: Path) -> None:
    engine = _engine(root, user={"permissions": {"deny": ["Read(.env)"]}})
    assert engine.decide("read_file", {"path": ".env "}, M.FULL_AUTONOMY).behavior == "deny"


# -- W1-R4: cp/mv destinations are vetted, options and glob/quote tricks included ---


@pytest.mark.parametrize(
    "command",
    [
        "cp --target-directory=.git/hooks pre-commit",
        "cp -t .git/hooks pre-commit",
        "cp -t.git/hooks pre-commit",
        "mv --target-directory=.orin settings.json",
        "mv -t .claude x",
        '"cp" -t ".git/hooks" x',
        "mv '.git' x",
        'mv ".g"it x',
        "mv .\\git x",
        "mv .git/config x",
        "cp x .git/hooks/pre-commit",
    ],
)
def test_acceptedits_never_auto_allows_a_protected_transfer(root: Path, command: str) -> None:
    engine = _engine(root)
    decision = engine.decide("run_command", {"command": command}, M.ACCEPT_EDITS)
    assert decision.behavior == "ask" and decision.requires_human is True, command


@pytest.mark.parametrize("command", ["cp evil.json .ori?/settings.json", "mv .gi? x", "cp a [.]git/x", "mv *.py .."])
def test_acceptedits_asks_when_a_glob_cannot_be_canonicalized(root: Path, command: str) -> None:
    engine = _engine(root)
    assert engine.decide("run_command", {"command": command}, M.ACCEPT_EDITS).behavior == "ask", command


def test_plain_inside_transfer_stays_allowed(root: Path) -> None:
    engine = _engine(root)
    assert engine.decide("run_command", {"command": "cp a.txt b.txt"}, M.ACCEPT_EDITS).behavior == "allow"
    assert engine.decide("run_command", {"command": "mv src/a.py src/b.py"}, M.ACCEPT_EDITS).behavior == "allow"
