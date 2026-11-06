from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest

from agentos.installation import versions
from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import cli
from agentos.launcher.ui import Console

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(
        tmp_path / "config",
        tmp_path / "data",
        tmp_path / "logs",
        tmp_path / "cache",
        tmp_path / "run",
    ).ensure()


def _installed_profile(tmp_path: Path, version: str = "0.4.0") -> RuntimeProfile:
    root = tmp_path / "Orin"
    runtime = root / version / "resources" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return RuntimeProfile("installed", runtime, version, None)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)


def test_detach_respawns_with_breakaway_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.os, "name", "nt")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> object:
        calls.append((list(command), kwargs))
        return object()

    arguments = cli.build_parser().parse_args(["update", "--apply", "--restart", "--detach"])
    assert arguments.detach is True

    code = cli.command_update(
        _paths(tmp_path),
        _installed_profile(tmp_path),
        Console(io.StringIO(), colour=False),
        arguments,
        popen=fake_popen,
    )

    assert code == 0
    assert len(calls) == 1
    spawned_argv, spawned_kwargs = calls[0]
    assert "--detach" not in spawned_argv
    assert spawned_argv[-3:] == ["update", "--apply", "--restart"]
    flags = spawned_kwargs["creationflags"]
    assert flags & CREATE_BREAKAWAY_FROM_JOB
    assert flags & CREATE_NEW_PROCESS_GROUP
    assert flags & CREATE_NO_WINDOW


def test_detach_falls_back_when_breakaway_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.os, "name", "nt")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> object:
        calls.append((list(command), kwargs))
        if len(calls) == 1:
            raise PermissionError(5, "Access is denied")
        return object()

    code = cli.command_update(
        _paths(tmp_path),
        _installed_profile(tmp_path),
        Console(io.StringIO(), colour=False),
        argparse.Namespace(apply=True, restart=True, detach=True),
        popen=fake_popen,
    )

    assert code == 0
    assert len(calls) == 2
    first_argv, first_kwargs = calls[0]
    second_argv, second_kwargs = calls[1]
    assert "--detach" not in first_argv
    assert first_argv == second_argv
    assert first_kwargs["creationflags"] & CREATE_BREAKAWAY_FROM_JOB
    assert not (second_kwargs["creationflags"] & CREATE_BREAKAWAY_FROM_JOB)
    assert second_kwargs["creationflags"] & CREATE_NEW_PROCESS_GROUP
    assert second_kwargs["creationflags"] & CREATE_NO_WINDOW


def test_electron_update_command_passes_detach_and_supervised_children_never_breakaway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    main_cjs = (repo_root / "desktop" / "electron" / "main.cjs").read_text(encoding="utf-8")
    assert "'update', '--apply', '--restart', '--detach'" in main_cjs

    from agentos.launcher import processes

    monkeypatch.setattr(processes, "_WINDOWS", True)
    spawned_flags: list[int] = []

    class _FakePopen:
        pid = 12345

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            spawned_flags.append(int(kwargs.get("creationflags", 0)))

    class _FakeGroup:
        def adopt(self, pid: int) -> bool:
            return True

    monkeypatch.setattr(processes.subprocess, "Popen", _FakePopen)
    child = processes.SupervisedProcess("gateway", ("orin", "internal-service", "gateway"), tmp_path / "gw.log")
    child.start(environment={}, cwd=tmp_path, group=_FakeGroup())  # type: ignore[arg-type]
    child._close_log()

    assert len(spawned_flags) == 1
    assert not (spawned_flags[0] & CREATE_BREAKAWAY_FROM_JOB)


def test_second_apply_exits_when_lock_held_by_live_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    from agentos.installation.updater import Updater

    paths = _paths(tmp_path)
    profile = _installed_profile(tmp_path)
    lock_file = paths.run / "update-apply.lock"
    lock_file.write_text(str(os.getpid()), encoding="utf-8")

    apply_called: list[bool] = []
    monkeypatch.setattr(Updater, "apply_prepared", lambda self: apply_called.append(True))

    code = cli.command_update(
        paths,
        profile,
        Console(io.StringIO(), colour=False),
        argparse.Namespace(apply=True, restart=False),
    )

    assert code == 0
    assert apply_called == []
    assert lock_file.is_file()
    assert lock_file.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_apply_replaces_stale_lock_from_dead_or_expired_pid_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time
    from agentos.installation.updater import UpdateResult, Updater

    paths = _paths(tmp_path)
    profile = _installed_profile(tmp_path)
    lock_file = paths.run / "update-apply.lock"

    # Case 1: dead PID is replaced and removed in finally.
    lock_file.write_text("99999999", encoding="utf-8")
    seen_lock_pids: list[str] = []

    def fake_apply(self: Updater) -> UpdateResult:
        seen_lock_pids.append(lock_file.read_text(encoding="utf-8").strip())
        return UpdateResult("updated", "0.4.0", "0.5.0")

    monkeypatch.setattr(Updater, "apply_prepared", fake_apply)
    code = cli.command_update(
        paths,
        profile,
        Console(io.StringIO(), colour=False),
        argparse.Namespace(apply=True, restart=False),
    )

    assert code == 0
    assert seen_lock_pids == [str(os.getpid())]
    assert not lock_file.exists()

    # Case 2: reused live PID on an expired lock file (> max age) is also replaced.
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    expired = time.time() - 3600
    os.utime(lock_file, (expired, expired))
    seen_lock_pids.clear()

    code_reused = cli.command_update(
        paths,
        profile,
        Console(io.StringIO(), colour=False),
        argparse.Namespace(apply=True, restart=False),
    )

    assert code_reused == 0
    assert seen_lock_pids == [str(os.getpid())]
    assert not lock_file.exists()


