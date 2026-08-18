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


def test_status_flags_an_update_when_the_latest_release_is_newer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _root = _profile(tmp_path, version="0.1.11")
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    monkeypatch.setattr(versions, "_latest_release", lambda: {"version": "0.1.12", "url": "https://example.test"})

    assert versions.read_installation_status(profile)["update_available"] is True


def test_status_does_not_flag_an_update_when_already_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _root = _profile(tmp_path, version="0.1.12")
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    monkeypatch.setattr(versions, "_latest_release", lambda: {"version": "0.1.12", "url": "https://example.test"})

    assert versions.read_installation_status(profile)["update_available"] is False


def test_status_does_not_flag_an_update_when_the_release_lookup_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _root = _profile(tmp_path, version="0.1.12")
    monkeypatch.setattr(versions, "_latest_release", lambda: None)

    assert versions.read_installation_status(profile)["update_available"] is False


def test_start_update_runs_the_installer_and_reports_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _root = _profile(tmp_path)
    installer = profile.root / "install.ps1"
    installer.write_text("# fake installer", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(command, *, check, capture_output, text, stdin, timeout):
        captured["command"] = command
        captured["stdin"] = stdin
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(versions.subprocess, "run", fake_run)

    assert versions.start_update(profile) == {"started": True}
    assert str(installer) in captured["command"]
    # -NoDesktopShortcut: this call has no terminal attached to answer
    # install.ps1's "create a shortcut?" prompt; must skip it, not hang on it.
    assert "-NoDesktopShortcut" in captured["command"]
    assert captured["stdin"] is versions.subprocess.DEVNULL


def test_start_update_is_unavailable_outside_a_packaged_install() -> None:
    profile = RuntimeProfile("development", Path("C:/repo"), "0.1.12", Path("C:/repo"))

    with pytest.raises(ValueError, match="packaged"):
        versions.start_update(profile)


def test_start_update_surfaces_the_installer_stderr_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _root = _profile(tmp_path)
    (profile.root / "install.ps1").write_text("# fake installer", encoding="utf-8")
    monkeypatch.setattr(
        versions.subprocess, "run",
        lambda command, *, check, capture_output, text, stdin, timeout: type("Result", (), {"returncode": 1, "stdout": "", "stderr": "hash mismatch"})(),
    )

    with pytest.raises(RuntimeError, match="hash mismatch"):
        versions.start_update(profile)


def test_start_update_turns_an_installer_timeout_into_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _root = _profile(tmp_path)
    (profile.root / "install.ps1").write_text("# fake installer", encoding="utf-8")

    def fake_run(command, *, check, capture_output, text, stdin, timeout):
        raise versions.subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    monkeypatch.setattr(versions.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="5 minutes"):
        versions.start_update(profile)
