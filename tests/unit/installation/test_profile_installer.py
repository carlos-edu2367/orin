from pathlib import Path

import pytest

from agentos.installation import profile as profile_module
from agentos.installation.profile import RuntimeProfile


def test_installer_is_install_ps1_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_module.os, "name", "nt")
    (tmp_path / "install.ps1").write_text("# installer", encoding="utf-8")
    (tmp_path / "install.sh").write_text("# installer", encoding="utf-8")

    profile = RuntimeProfile("installed", tmp_path, "1.0.0", None)

    assert profile.installer == tmp_path / "install.ps1"


def test_installer_is_install_sh_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_module.os, "name", "posix")
    (tmp_path / "install.ps1").write_text("# installer", encoding="utf-8")
    (tmp_path / "install.sh").write_text("# installer", encoding="utf-8")

    profile = RuntimeProfile("installed", tmp_path, "1.0.0", None)

    assert profile.installer == tmp_path / "install.sh"
