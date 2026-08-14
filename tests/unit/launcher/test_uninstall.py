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


def test_installed_uninstall_schedules_the_packaged_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    installer = runtime / "install.ps1"
    installer.write_text("# packaged installer", encoding="utf-8")
    invoked: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda command, check: invoked.append(command) or Result())

    assert cli.command_uninstall(paths, RuntimeProfile("installed", runtime, "1.0.0", None), Console(io.StringIO(), colour=False)) == 0
    assert invoked == [[
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer),
        "-Uninstall", "-Force", "-WaitForPid", str(os.getpid()),
    ]]


def test_uninstall_refuses_to_delete_a_source_checkout(tmp_path: Path) -> None:
    stream = io.StringIO()
    profile = RuntimeProfile("development", tmp_path, "1.0.0", tmp_path)

    assert cli.command_uninstall(_paths(tmp_path), profile, Console(stream, colour=False)) == 2
    assert "will not delete this source checkout" in stream.getvalue()
