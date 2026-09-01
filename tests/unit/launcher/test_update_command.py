from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import cli
from agentos.launcher.ui import Console


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def test_update_invokes_the_platform_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    installer_name = "install.ps1" if os.name == "nt" else "install.sh"
    installer = runtime / installer_name
    installer.write_text("# packaged installer", encoding="utf-8")
    invoked: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda command, check: invoked.append(command) or Result())

    assert cli.command_update(paths, RuntimeProfile("installed", runtime, "1.0.0", None), Console(io.StringIO(), colour=False)) == 0
    if os.name == "nt":
        assert invoked == [["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)]]
    else:
        assert invoked == [["bash", str(installer)]]


def test_update_reports_a_missing_installer(tmp_path: Path) -> None:
    stream = io.StringIO()
    paths = _paths(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    assert cli.command_update(paths, RuntimeProfile("installed", runtime, "1.0.0", None), Console(stream, colour=False)) == 1
    assert "release installer is missing" in stream.getvalue()
