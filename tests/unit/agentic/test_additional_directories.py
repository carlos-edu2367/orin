"""W4: additional working directories — validation, the workspace sandbox, the tools and the permission engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.directories import (
    MAX_ADDITIONAL_DIRECTORIES, DirectoryError, check_directory, settings_directories, working_directories,
)
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.permissions.engine import PermissionEngine
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir


@pytest.fixture()
def dirs(tmp_path: Path):
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    (other / "src").mkdir(parents=True)
    workspace.mkdir()
    (other / "src" / "lib.py").write_bytes(b"def answer():\n    return 41\n")
    return workspace, other


def test_a_real_folder_outside_the_workspace_is_accepted(dirs) -> None:
    workspace, other = dirs
    assert check_directory(str(other), workspace_root=workspace) == other.resolve()


@pytest.mark.parametrize("make", [
    lambda w, o: str(Path(o.anchor)),                  # a drive / filesystem root
    lambda w, o: str(Path.home()),                     # the home folder
    lambda w, o: str(Path.home().parent),              # a folder that contains it
    lambda w, o: str(orin_user_dir()),                 # Orin's own folder
    lambda w, o: str(w / "sub"),                       # already inside the workspace (created below)
    lambda w, o: str(o / "missing"),                   # does not exist
    lambda w, o: str(o / "src" / "lib.py"),            # a file
    lambda w, o: "\\\\server\\share",                  # a network path
    lambda w, o: "",
])
def test_unsafe_folders_are_refused(dirs, make) -> None:
    workspace, other = dirs
    (workspace / "sub").mkdir()
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    with pytest.raises(DirectoryError):
        check_directory(make(workspace, other), workspace_root=workspace)


def test_credential_folders_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    for folder in (home / ".ssh" / "keys", home / "projects" / "app", home / ".config" / "gcloud"):
        folder.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for refused in (home / ".ssh", home / ".ssh" / "keys", home / ".config", home / ".config" / "gcloud"):
        with pytest.raises(DirectoryError):
            check_directory(str(refused), workspace_root=workspace)
    assert check_directory(str(home / "projects" / "app"), workspace_root=workspace) == (home / "projects" / "app").resolve()


def test_the_list_is_deduplicated_nested_folders_collapse_and_bad_entries_are_skipped(dirs, tmp_path: Path) -> None:
    workspace, other = dirs
    found = working_directories([str(other / "src"), str(other), str(other), str(Path.home()), 7, str(tmp_path / "nope")], workspace_root=workspace)
    assert found == (other.resolve(),)
    many = []
    for index in range(MAX_ADDITIONAL_DIRECTORIES + 3):
        folder = tmp_path / f"d{index}"
        folder.mkdir()
        many.append(str(folder))
    assert len(working_directories(many, workspace_root=workspace)) == MAX_ADDITIONAL_DIRECTORIES


def test_the_workspace_resolves_inside_additional_directories_only(dirs, tmp_path: Path) -> None:
    workspace_root, other = dirs
    workspace = ConversationWorkspace.at_root(workspace_root)
    target = str(other / "src" / "lib.py")
    with pytest.raises(WorkspaceError):
        workspace.resolve(target)  # not a working directory yet
    workspace.additional_roots = (other.resolve(),)
    assert workspace.resolve(target) == (other / "src" / "lib.py").resolve()
    assert workspace.key(workspace.resolve(target)) == (other / "src" / "lib.py").resolve().as_posix()
    assert workspace.key(workspace_root / "a.txt") == "a.txt"
    with pytest.raises(WorkspaceError):
        workspace.resolve(str(other / ".." / "workspace2" / "x"))
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(WorkspaceError):
        workspace.resolve(str(tmp_path / "elsewhere" / "x.txt"))
    with pytest.raises(WorkspaceError):
        workspace.key(tmp_path / "elsewhere" / "x.txt")


def test_the_file_tools_work_in_an_additional_directory(dirs) -> None:
    workspace_root, other = dirs
    workspace = ConversationWorkspace.at_root(workspace_root)
    workspace.additional_roots = (other.resolve(),)
    toolset = AgentToolset(workspace, public_tool_names=True)
    lib = str((other / "src" / "lib.py").resolve())
    read = toolset.invoke("Read", {"file_path": lib})
    assert read.status == "succeeded", read.content
    edited = toolset.invoke("Edit", {"file_path": lib, "old_string": "41", "new_string": "42"})
    assert edited.status == "succeeded", edited.content
    assert b"return 42" in (other / "src" / "lib.py").read_bytes()
    written = toolset.invoke("Write", {"file_path": str((other / "notes.md").resolve()), "content": "hello\n"})
    assert written.status == "succeeded", written.content
    globbed = toolset.invoke("Glob", {"pattern": "**/*.py", "path": str(other.resolve())})
    assert globbed.status == "succeeded" and any(name.endswith("lib.py") for name in globbed.data["tool_output"]["filenames"])
    grepped = toolset.invoke("Grep", {"pattern": "return 42", "path": str(other.resolve()), "output_mode": "files_with_matches"})
    assert grepped.status == "succeeded", grepped.content
    assert "lib.py" in grepped.content


def _engine(root: Path, extra: tuple[Path, ...]) -> PermissionEngine:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"permissions": {}}), encoding="utf-8")
    return PermissionEngine(root, load_effective_settings(root, trusted=False), shell="cmd", home=root.parent / "home", additional_roots=extra)


def test_accept_edits_covers_additional_directories_but_their_protected_paths_still_ask(dirs) -> None:
    workspace, other = dirs
    engine = _engine(workspace, (other.resolve(),))
    inside = engine.decide("edit_file", {"path": str(other / "src" / "lib.py")}, "accept_edits")
    assert inside.behavior == "allow"
    hook = engine.decide("write_file", {"path": str(other / ".git" / "hooks" / "pre-commit")}, "accept_edits")
    assert hook.behavior == "ask" and hook.requires_human
    outside = _engine(workspace, ()).decide("edit_file", {"path": str(other / "src" / "lib.py")}, "accept_edits")
    assert outside.behavior == "ask"


def test_a_repository_adds_directories_only_once_trusted(dirs) -> None:
    workspace, other = dirs
    (workspace / ".claude").mkdir()
    (workspace / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"additionalDirectories": [str(other)]}}), encoding="utf-8")
    assert settings_directories(load_effective_settings(workspace, trusted=False)) == []
    assert settings_directories(load_effective_settings(workspace, trusted=True)) == [str(other)]
