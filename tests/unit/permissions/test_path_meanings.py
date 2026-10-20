"""Lote 0 (W6): a deny or ask rule sees every file a tool path can name.

A full_autonomy workspace opens ``~/…`` in the home folder and, on POSIX, ``/…`` at the file-system root, while a
confined one reads both inside the workspace. The engine does not know which one the tool will use, so a deny or ask
rule holds when it matches any of the meanings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.models import CodePermissionMode as M
from agentos.permissions.engine import PermissionEngine
from agentos.permissions.paths import path_meanings
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    (folder / ".ssh").mkdir(parents=True)
    (folder / ".ssh" / "id_rsa").write_text("PRIVATE\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


def _engine(root: Path, permissions: dict) -> PermissionEngine:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"permissions": permissions}), encoding="utf-8")
    return PermissionEngine(root, load_effective_settings(root, trusted=False), shell="posix")


def test_the_unrestricted_tool_opens_the_home_file(home: Path, root: Path) -> None:
    # The premise: what the engine must see is the file a full_autonomy tool really opens.
    workspace = ConversationWorkspace.at_root(root, unrestricted=True)
    assert workspace.resolve("~/.ssh/id_rsa") == (home / ".ssh" / "id_rsa").resolve()


def test_path_meanings_lists_the_home_and_the_workspace_reading(home: Path, root: Path) -> None:
    meanings = path_meanings("~/.ssh/id_rsa", root)
    assert home / ".ssh" / "id_rsa" in meanings
    assert root / "~" / ".ssh" / "id_rsa" in meanings
    assert path_meanings("src/app.py", root) == [root / "src" / "app.py"]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_file", {"path": "~/.ssh/id_rsa"}),
        ("read_file", {"file_path": "~\\.ssh\\id_rsa"}),
        ("glob_files", {"pattern": "*", "path": "~/.ssh"}),
        ("search_files", {"pattern": "PRIVATE", "path": "~/.ssh"}),
    ],
)
def test_a_home_deny_rule_holds_for_a_tilde_path_in_full_autonomy(home: Path, root: Path, tool: str, args: dict) -> None:
    engine = _engine(root, {"deny": ["Read(~/.ssh/**)"]})
    assert engine.decide(tool, args, M.FULL_AUTONOMY).behavior == "deny"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("write_file", {"path": "~/.ssh/authorized_keys", "content": "x"}),
        ("edit_file", {"path": "~/.ssh/config", "old_string": "a", "new_string": "b"}),
        ("notebook_edit", {"notebook_path": "~/.ssh/n.ipynb", "new_source": "x"}),
    ],
)
def test_a_home_edit_deny_rule_holds_for_a_tilde_path(home: Path, root: Path, tool: str, args: dict) -> None:
    engine = _engine(root, {"deny": ["Edit(~/.ssh/**)"]})
    assert engine.decide(tool, args, M.FULL_AUTONOMY).behavior == "deny"


def test_a_home_ask_rule_holds_for_a_tilde_path(home: Path, root: Path) -> None:
    engine = _engine(root, {"ask": ["Read(~/.ssh/**)"]})
    assert engine.decide("read_file", {"path": "~/.ssh/id_rsa"}, M.ACCEPT_EDITS).behavior == "ask"


def test_a_workspace_deny_rule_still_holds_for_the_workspace_reading(home: Path, root: Path) -> None:
    engine = _engine(root, {"deny": ["Read(secrets/**)"]})
    assert engine.decide("read_file", {"path": "secrets/key.txt"}, M.FULL_AUTONOMY).behavior == "deny"
    assert engine.decide("read_file", {"path": "src/app.py"}, M.FULL_AUTONOMY).behavior == "allow"


def test_an_allow_rule_is_not_widened_by_the_other_meanings(home: Path, root: Path) -> None:
    # An allow keeps the workspace reading only: a second meaning never grants anything.
    engine = _engine(root, {"allow": ["Edit(~/notes/**)"]})
    assert engine.decide("write_file", {"path": "~/notes/a.md", "content": "x"}, M.ASK_PERMISSIONS).behavior == "ask"


@pytest.mark.skipif(os.name == "nt", reason="a leading / is a root path only on POSIX")
def test_an_absolute_deny_rule_holds_for_a_root_path_on_posix(tmp_path: Path, home: Path, root: Path) -> None:
    system = tmp_path / "system"
    system.mkdir()
    (system / "shadow").write_text("x\n", encoding="utf-8")
    engine = _engine(root, {"deny": [f"Read(/{system.as_posix()}/**)"]})
    target = str(system / "shadow")
    meanings = path_meanings(target, root)
    assert Path(target) in meanings and root.joinpath(*Path(target).parts[1:]) in meanings
    for tool, args in (
        ("read_file", {"path": target}),
        ("glob_files", {"pattern": "*", "path": str(system)}),
        ("search_files", {"pattern": "x", "path": str(system)}),
    ):
        assert engine.decide(tool, args, M.FULL_AUTONOMY).behavior == "deny", tool
