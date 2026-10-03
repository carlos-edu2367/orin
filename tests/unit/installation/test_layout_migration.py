from pathlib import Path

import pytest

from agentos.installation.layout import LAYOUT_MARKER, LayoutMigrationError, migrate_data_layout
from agentos.installation.paths import OrinPaths


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run")


def test_existing_workspaces_move_to_the_local_profile(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_1").mkdir(parents=True)
    (paths.data / "workspaces" / "chat_1" / "notes.md").write_text("oi", encoding="utf-8")

    assert migrate_data_layout(paths) is True

    moved = paths.user_workspaces("local-user") / "chat_1" / "notes.md"
    assert moved.read_text(encoding="utf-8") == "oi"
    assert not (paths.data / "workspaces").exists()
    assert (paths.data / LAYOUT_MARKER).is_file()


def test_running_twice_does_nothing_the_second_time(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_1").mkdir(parents=True)
    migrate_data_layout(paths)
    assert migrate_data_layout(paths) is False


def test_a_fresh_install_just_writes_the_marker(tmp_path):
    paths = _paths(tmp_path)
    assert migrate_data_layout(paths) is False
    assert (paths.data / LAYOUT_MARKER).is_file()


def test_an_interrupted_migration_resumes(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_2").mkdir(parents=True)
    paths.user_workspaces("local-user").mkdir(parents=True)
    (paths.user_workspaces("local-user") / "chat_1").mkdir()  # moved before the crash
    assert migrate_data_layout(paths) is True
    assert sorted(item.name for item in paths.user_workspaces("local-user").iterdir()) == ["chat_1", "chat_2"]


def test_a_name_present_on_both_sides_stops_the_boot(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_1").mkdir(parents=True)
    (paths.user_workspaces("local-user") / "chat_1").mkdir(parents=True)
    with pytest.raises(LayoutMigrationError):
        migrate_data_layout(paths)
    assert not (paths.data / LAYOUT_MARKER).exists()
