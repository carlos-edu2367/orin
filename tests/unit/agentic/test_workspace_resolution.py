from pathlib import Path

import pytest

from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError, resolve_workspace


def test_local_root_is_the_root_itself(tmp_path: Path) -> None:
    """A chosen folder must not gain a workspace-id subdirectory below it."""
    chosen = tmp_path / "projeto"
    chosen.mkdir()

    workspace = resolve_workspace("chat_abc", managed_root=tmp_path / "managed", local_root=str(chosen))

    assert workspace.root == chosen.resolve()
    assert not (chosen / "chat_abc").exists()


def test_without_a_local_root_the_managed_layout_is_unchanged(tmp_path: Path) -> None:
    managed = tmp_path / "managed"

    workspace = resolve_workspace("chat_abc", managed_root=managed, local_root=None)

    assert workspace.root == (managed / "chat_abc").resolve()


def test_containment_still_holds_under_a_local_root(tmp_path: Path) -> None:
    chosen = tmp_path / "projeto"
    chosen.mkdir()
    workspace = resolve_workspace("chat_abc", managed_root=tmp_path / "managed", local_root=str(chosen))

    with pytest.raises(WorkspaceError):
        workspace.resolve("../fora.txt")
    assert workspace.resolve("src/app.py") == (chosen / "src" / "app.py").resolve()


def test_at_root_does_not_create_a_missing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "nao-existe"

    workspace = ConversationWorkspace.at_root(missing)

    assert workspace.root == missing.resolve()
    assert not missing.exists()
