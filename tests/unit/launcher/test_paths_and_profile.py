from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths, find_repository_root
from agentos.installation.profile import RuntimeProfile
from agentos.version import __version__


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ORIN_HOME", "ORIN_CONFIG_DIR", "ORIN_DATA_DIR", "ORIN_LOGS_DIR", "ORIN_CACHE_DIR", "ORIN_RUN_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_layout_does_not_depend_on_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    from_one_directory = OrinPaths.resolve()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert OrinPaths.resolve() == from_one_directory
    assert from_one_directory.data == tmp_path / "home" / "data"
    assert from_one_directory.workspaces == tmp_path / "home" / "data" / "workspaces"


def test_individual_roots_override_the_home_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ORIN_LOGS_DIR", str(tmp_path / "somewhere-else"))

    paths = OrinPaths.resolve()

    assert paths.logs == tmp_path / "somewhere-else"
    assert paths.data == tmp_path / "home" / "data"


def test_development_layout_keeps_using_the_repository_directories() -> None:
    repository = find_repository_root()
    assert repository is not None, "this test runs inside the checkout"

    paths = OrinPaths.resolve()

    assert paths.data == repository / "data"
    assert paths.logs == repository / ".logs"


def test_child_environment_carries_the_whole_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_HOME", str(tmp_path))

    exported = OrinPaths.resolve().as_environment()

    assert set(exported) == {"ORIN_CONFIG_DIR", "ORIN_DATA_DIR", "ORIN_LOGS_DIR", "ORIN_CACHE_DIR", "ORIN_RUN_DIR"}
    assert all(Path(value).is_absolute() for value in exported.values())


def test_ensure_creates_every_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))

    paths = OrinPaths.resolve().ensure()

    for directory in (paths.config, paths.data, paths.logs, paths.cache, paths.run):
        assert directory.is_dir()


def test_service_command_reexecutes_the_launcher_not_a_source_tree() -> None:
    command = RuntimeProfile.detect().service_command("backend")

    assert command[-2:] == ("internal-service", "backend")
    assert "uvicorn" not in " ".join(command)


def test_runtime_profile_uses_the_embedded_release_version() -> None:
    assert RuntimeProfile.detect().version == __version__ == "0.1.13"


def test_migrations_are_resolved_from_the_package() -> None:
    migrations = RuntimeProfile.detect().migrations

    assert (migrations / "env.py").is_file()
    assert migrations.is_absolute()


def test_frozen_build_reports_an_installed_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "orin.exe"))

    profile = RuntimeProfile.detect()

    assert profile.kind == "installed"
    assert profile.repository is None
    assert profile.root == tmp_path
    assert profile.service_command("worker") == (str(tmp_path / "orin.exe"), "internal-service", "worker")


def test_installed_profile_has_no_external_service_definition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "orin.exe"))

    assert not hasattr(RuntimeProfile.detect(), "compose_file")


def test_user_layout_is_outside_the_installation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # An installation directory that can be replaced wholesale is the whole
    # reason `orin update` will be able to keep user data.
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "install" / "orin.exe"))
    monkeypatch.setattr("agentos.installation.paths.find_repository_root", lambda *_: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "roaming"))

    paths = OrinPaths.resolve()
    installation = RuntimeProfile.detect().root

    assert os.path.commonpath([str(paths.data), str(installation)]) != str(installation)
