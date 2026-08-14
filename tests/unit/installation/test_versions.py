from __future__ import annotations

from pathlib import Path

import pytest

from agentos.installation.profile import RuntimeProfile
from agentos.installation import versions


def _profile(tmp_path: Path, version: str = "0.1.12") -> tuple[RuntimeProfile, Path]:
    version_root = tmp_path / version
    runtime = version_root / "resources" / "runtime"
    runtime.mkdir(parents=True)
    return RuntimeProfile("installed", runtime, version, None), tmp_path


def test_status_lists_only_semver_version_directories_and_marks_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, root = _profile(tmp_path)
    (root / "0.1.11").mkdir()
    (root / "notes").mkdir()
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    monkeypatch.setattr(versions, "_latest_release", lambda: {"version": "0.1.12", "url": "https://github.com/carlos-edu2367/orin/releases/tag/v0.1.12"})

    status = versions.read_installation_status(profile)

    assert [item["version"] for item in status["installed_versions"]] == ["0.1.12", "0.1.11"]
    assert status["installed_versions"][0]["is_current"] is True
    assert status["installed_versions"][0]["removable"] is False
    assert status["installed_versions"][1]["removable"] is True


def test_remove_version_cannot_remove_current_or_escape_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, root = _profile(tmp_path)
    (root / "0.1.11").mkdir()
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)

    assert versions.remove_installed_version("0.1.11", profile) == {"removed_version": "0.1.11"}
    with pytest.raises(ValueError, match="current"):
        versions.remove_installed_version("0.1.12", profile)
    with pytest.raises(ValueError, match="invalid"):
        versions.remove_installed_version("..", profile)


def test_development_status_does_not_advertise_removable_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = RuntimeProfile("development", Path("C:/repo"), "0.1.12", Path("C:/repo"))
    monkeypatch.setattr(versions, "_latest_release", lambda: None)

    status = versions.read_installation_status(profile)

    assert status["installation_kind"] == "development"
    assert status["installed_versions"] == []
