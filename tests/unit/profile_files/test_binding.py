import os
from pathlib import Path

import pytest

from agentos.local_workspace import FolderRejected
from agentos.profile_files.binding import ProfileFolderRejected, inspect_profile_folder, relative_display, resolve_inside


def test_relative_paths_resolve_inside_the_root(tmp_path):
    (tmp_path / "proj" / "app").mkdir(parents=True)
    assert resolve_inside(tmp_path, "proj/app") == (tmp_path / "proj" / "app").resolve()
    assert resolve_inside(tmp_path, "proj\\app") == (tmp_path / "proj" / "app").resolve()
    assert resolve_inside(tmp_path, "") == tmp_path.resolve()
    assert resolve_inside(tmp_path, ".") == tmp_path.resolve()


@pytest.mark.parametrize("raw", ["../x", "proj/../../x", "/etc", "C:\\Windows", "C:/Windows", "\\\\server\\share", "proj\0x"])
def test_escapes_are_refused(tmp_path, raw):
    with pytest.raises(ProfileFolderRejected):
        resolve_inside(tmp_path, raw)


def test_the_rejection_is_a_folder_rejection(tmp_path):
    with pytest.raises(FolderRejected):
        resolve_inside(tmp_path, "../x")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_root_is_refused(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProfileFolderRejected):
        resolve_inside(root, "link")


def test_inspection_reports_a_relative_path_without_risk(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "a.txt").write_text("a", encoding="utf-8")
    inspection = inspect_profile_folder(tmp_path, "proj")
    assert (inspection.path, inspection.is_directory, inspection.writable, inspection.entry_count, inspection.risk) == ("proj", True, True, 1, "none")
    missing = inspect_profile_folder(tmp_path, "nope")
    assert (missing.exists, missing.is_directory) == (False, False)


def test_relative_display(tmp_path):
    (tmp_path / "proj").mkdir()
    assert relative_display(tmp_path, tmp_path / "proj") == "proj"
    assert relative_display(tmp_path, tmp_path) == "."
    assert relative_display(tmp_path, tmp_path.parent) is None
