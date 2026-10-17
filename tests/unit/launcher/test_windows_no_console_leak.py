from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentos.installation.updater import Updater, runtime_relative_path
from agentos.launcher import cli, processes
from agentos.omniroute import process_manager


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)


def test_relaunch_desktop_uses_create_no_window_without_detached_process_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MSDN: CREATE_NO_WINDOW is ignored if combined with DETACHED_PROCESS."""
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP, raising=False)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", DETACHED_PROCESS, raising=False)

    runtime = tmp_path / "current" / runtime_relative_path()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text("", encoding="utf-8")

    captured: list[dict[str, object]] = []

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured.append({"command": command, **kwargs})
        return object()

    assert cli._relaunch_desktop(tmp_path, sleep=lambda _: None, popen=fake_popen) is True
    assert len(captured) == 1
    flags = int(captured[0]["creationflags"])  # type: ignore[arg-type]
    assert flags & CREATE_NO_WINDOW, "must set CREATE_NO_WINDOW on Windows"
    assert flags & CREATE_NEW_PROCESS_GROUP, "must set CREATE_NEW_PROCESS_GROUP on Windows"
    assert not (flags & DETACHED_PROCESS), (
        "must NOT set DETACHED_PROCESS alongside CREATE_NO_WINDOW because Win32 ignores CREATE_NO_WINDOW when DETACHED_PROCESS is set"
    )


def test_supervised_process_start_sets_create_no_window_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Supervisor runs without a console after update restart, children (backend, worker, scheduler) must not allocate console windows."""
    monkeypatch.setattr(processes, "_WINDOWS", True)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP, raising=False)

    captured_flags: list[int] = []

    class DummyPopen:
        pid = 12345

        def __init__(self, _cmd: list[str], **kwargs: object) -> None:
            captured_flags.append(int(kwargs.get("creationflags", 0)))  # type: ignore[arg-type]

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(processes.subprocess, "Popen", DummyPopen)

    group = processes.ProcessGroup()
    monkeypatch.setattr(group, "adopt", lambda _pid: True)
    child = processes.SupervisedProcess("backend", ("orin.exe", "_internal", "backend"), tmp_path / "backend.log")
    try:
        child.start(environment={}, cwd=tmp_path, group=group)
    finally:
        child.stop()

    assert len(captured_flags) == 1
    assert captured_flags[0] & CREATE_NO_WINDOW
    assert captured_flags[0] & CREATE_NEW_PROCESS_GROUP


def test_updater_version_check_passes_create_no_window_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agentos.installation.updater.os.name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)

    captured_kwargs: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="orin 0.5.0\n", stderr="")

    monkeypatch.setattr("agentos.installation.updater.subprocess.run", fake_run)

    updater = Updater(versions_root=tmp_path, current_version="0.4.0", platform="windows-x64")
    updater._run_version_check(tmp_path / "orin.exe", "0.5.0", step="validate")

    assert len(captured_kwargs) == 1
    flags = int(captured_kwargs[0].get("creationflags", 0))  # type: ignore[arg-type]
    assert flags & CREATE_NO_WINDOW


def test_omniroute_process_manager_passes_create_no_window_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_manager.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)

    captured_kwargs: list[dict[str, object]] = []

    def fake_popen(_cmd: object, **kwargs: object) -> object:
        captured_kwargs.append(kwargs)
        return object()

    monkeypatch.setattr(process_manager, "Popen", fake_popen)
    process_manager.OmniRouteProcessManager._spawn(("omniroute.cmd",))

    assert len(captured_kwargs) == 1
    flags = int(captured_kwargs[0].get("creationflags", 0))  # type: ignore[arg-type]
    assert flags & CREATE_NO_WINDOW


def test_electron_main_does_not_combine_detached_and_windows_hide_on_win32() -> None:
    main_cjs = (Path(__file__).resolve().parents[3] / "desktop" / "electron" / "main.cjs").read_text(encoding="utf-8")
    assert "detached: process.platform !== 'win32'" in main_cjs
