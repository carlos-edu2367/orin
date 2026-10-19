"""W4: an absolute path inside the workspace means that file, for the tools and for the permission engine."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.pathsafety import absolute_inside, is_drive_path
from agentos.permissions.engine import PermissionEngine
from agentos.permissions.paths import workspace_path
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir


def _workspace(tmp_path: Path) -> ConversationWorkspace:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    (workspace.root / "src").mkdir()
    (workspace.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return workspace


def test_an_absolute_path_inside_the_workspace_is_that_file(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    expected = (workspace.root / "src" / "a.py").resolve()
    assert workspace.resolve(str(workspace.root / "src" / "a.py")) == expected
    assert workspace.resolve(str(workspace.root / "src" / "a.py").replace("\\", "/")) == expected
    assert workspace.resolve(str(workspace.root)) == workspace.root


@pytest.mark.skipif(os.name != "nt", reason="drive letters and case folding are Windows behaviour")
def test_case_and_separators_do_not_matter_on_windows(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    shouted = str(workspace.root / "SRC" / "A.PY").upper()
    assert workspace.resolve(shouted) == (workspace.root / "src" / "a.py").resolve()


def test_a_drive_path_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        workspace.resolve("C:/Windows/win.ini")
    if os.name == "nt":
        outside = tmp_path / "secret.txt"
        outside.write_text("s", encoding="utf-8")
        with pytest.raises(WorkspaceError):
            workspace.resolve(str(outside))


def test_dot_dot_in_an_absolute_path_is_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        workspace.resolve(str(workspace.root / "src" / ".." / ".." / "x.txt"))


def test_a_leading_slash_that_is_not_under_the_root_stays_workspace_relative(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert workspace.resolve("/notes/a.md") == workspace.root / "notes" / "a.md"
    assert workspace.resolve("\\src\\a.py") == (workspace.root / "src" / "a.py").resolve()


def test_the_helper_is_lexical_and_strict(tmp_path: Path) -> None:
    root = tmp_path / "w"
    assert absolute_inside(str(root / "a" / "b.txt"), root) == "a/b.txt"
    assert absolute_inside(str(root), root) == ""
    assert absolute_inside("a/b.txt", root) is None
    assert absolute_inside(str(root) + "-other/x", root) is None
    assert absolute_inside(r"\\server\share\x", root) is None
    assert is_drive_path("c:\\x") and is_drive_path("D:/x") and not is_drive_path("/x") and not is_drive_path("x:y")


def test_the_permission_engine_sees_the_same_file(tmp_path: Path) -> None:
    root = (tmp_path / "w").resolve()
    root.mkdir()
    assert workspace_path(str(root / "src" / "a.py"), root) == root / "src" / "a.py"
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Edit(src/**)"]}}), encoding="utf-8")
    engine = PermissionEngine(root, load_effective_settings(root, trusted=False), shell="posix", home=root.parent / "home")
    decision = engine.decide("edit_file", {"path": str(root / "src" / "a.py")}, "accept_edits")
    assert decision.behavior == "deny"
