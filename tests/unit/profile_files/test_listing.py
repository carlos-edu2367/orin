import pytest

from agentos.profile_files.binding import ProfileFolderRejected
from agentos.profile_files.listing import list_folder, make_folder


def test_listing_puts_folders_first_and_reports_sizes(tmp_path):
    (tmp_path / "b-dir").mkdir()
    (tmp_path / "a-dir").mkdir()
    (tmp_path / "z.txt").write_text("123", encoding="utf-8")
    listing = list_folder(tmp_path, "")
    assert listing["path"] == "."
    assert [(item["name"], item["kind"]) for item in listing["entries"]] == [("a-dir", "directory"), ("b-dir", "directory"), ("z.txt", "file")]
    assert listing["entries"][2]["bytes"] == 3
    assert listing["truncated"] is False


def test_listing_a_missing_folder_is_rejected(tmp_path):
    with pytest.raises(ProfileFolderRejected):
        list_folder(tmp_path, "nope")


def test_make_folder_creates_a_child_and_returns_its_relative_path(tmp_path):
    assert make_folder(tmp_path, "", "projetos") == "projetos"
    assert make_folder(tmp_path, "projetos", "app") == "projetos/app"
    assert (tmp_path / "projetos" / "app").is_dir()


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "x" * 256])
def test_make_folder_refuses_unsafe_names(tmp_path, name):
    with pytest.raises(ProfileFolderRejected):
        make_folder(tmp_path, "", name)
