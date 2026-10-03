from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run")


def test_user_roots_live_under_data_users(tmp_path):
    paths = _paths(tmp_path)
    assert paths.user_root("local-user") == tmp_path / "data" / "users" / "local-user"
    assert paths.user_workspaces("usr_ab12") == tmp_path / "data" / "users" / "usr_ab12" / "workspaces"
    assert paths.user_files("usr_ab12") == tmp_path / "data" / "users" / "usr_ab12" / "files"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("user_id", ["", "../etc", "a/b", "a b", "x" * 65, "ção"])
def test_unsafe_user_ids_are_refused(tmp_path, user_id):
    with pytest.raises(ValueError):
        _paths(tmp_path).user_root(user_id)
