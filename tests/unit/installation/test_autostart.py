from __future__ import annotations

from pathlib import Path

import pytest

from agentos.installation.autostart import Autostart, autostart_entry, windows_command
from agentos.installation.installer import InstallOptions, Installer


def test_not_supported_outside_a_packaged_install():
    autostart = Autostart(None, system="linux-x64")
    assert autostart.status().supported is False
    with pytest.raises(RuntimeError):
        autostart.set(True)


def test_linux_entry_points_at_current_and_starts_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "Orin" / "versions"
    autostart = Autostart(root, system="linux-x64")
    assert autostart.status().enabled is False

    assert autostart.set(True).enabled is True
    entry = (tmp_path / "config" / "autostart" / "orin.desktop").read_text(encoding="utf-8")
    assert f'Exec="{root / "current" / "resources" / "runtime" / "orin"}" --desktop --background' in entry
    assert "Terminal=false" in entry

    assert autostart.set(False).enabled is False
    assert not (tmp_path / "config" / "autostart" / "orin.desktop").exists()
    assert autostart.set(False).enabled is False  # idempotent


def test_windows_command_is_hidden_and_survives_updates():
    root = Path("C:/Users/ana/AppData/Local/Programs/Orin")
    command = windows_command(root)
    assert "-WindowStyle Hidden" in command
    assert "'--desktop','--background'" in command
    assert "current" in command and "orin.exe" in command


def test_installer_enables_autostart_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    calls: list[bool] = []
    monkeypatch.setattr(Autostart, "set", lambda self, enabled: calls.append(enabled))

    class Integration:
        def integrate(self, options, version):
            return None

        def remove(self, options):
            return None

    class Updater:
        def __init__(self, **kwargs):
            pass

        def active_version(self):
            return None

        def run(self, version):
            from types import SimpleNamespace
            return SimpleNamespace(status="updated", version="1.0.0", release_url=None, notes=None, downloaded_bytes=0, seconds=0.0)

    options = InstallOptions(root=tmp_path / "Orin" / "versions", bin_root=tmp_path / "bin", background=True)
    Installer(options, integration=Integration(), updater_factory=Updater, platform="linux-x64").run()
    assert calls == [True]
