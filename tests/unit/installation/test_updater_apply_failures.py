from __future__ import annotations

import argparse
import io
import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from agentos.installation import updater as updater_module
from agentos.installation import versions
from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.installation.updater import (
    PREPARED_FILE,
    READY_SUFFIX,
    UpdateError,
    Updater,
    point_current,
    runtime_relative_path,
)
from agentos.launcher import cli
from agentos.launcher.ui import Console


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(
        tmp_path / "config",
        tmp_path / "data",
        tmp_path / "logs",
        tmp_path / "cache",
        tmp_path / "run",
    ).ensure()


def _setup_installation(tmp_path: Path, current: str = "0.11.6", prepared: str | None = "0.12.1") -> Path:
    root = tmp_path / "Orin"
    current_runtime = root / current / runtime_relative_path()
    current_runtime.parent.mkdir(parents=True, exist_ok=True)
    current_runtime.write_bytes(b"orin-binary")
    point_current(root, root / current)

    if prepared is not None:
        ready_dir = root / f"{prepared}{READY_SUFFIX}"
        ready_runtime = ready_dir / runtime_relative_path()
        ready_runtime.parent.mkdir(parents=True, exist_ok=True)
        ready_runtime.write_bytes(b"orin-binary-new")
        (root / PREPARED_FILE).write_text(
            json.dumps(
                {
                    "version": prepared,
                    "sha256": "a" * 64,
                    "release_url": f"https://example.test/releases/tag/v{prepared}",
                    "notes": "Correções",
                }
            ),
            encoding="utf-8",
        )
    return root


def test_os_error_during_replace_is_recorded_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_installation(tmp_path, current="0.11.6", prepared="0.12.1")
    monkeypatch.setattr(Updater, "_run_version_check", lambda self, runtime, version, *, step: None)

    real_replace = os.replace
    replace_attempts: list[tuple[Path, Path]] = []
    sleeps: list[float] = []

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        src_path, dst_path = Path(src), Path(dst)
        if src_path.name == f"0.12.1{READY_SUFFIX}":
            replace_attempts.append((src_path, dst_path))
            raise PermissionError(13, "Acesso negado")
        real_replace(src, dst)

    monkeypatch.setattr(updater_module.os, "replace", flaky_replace)

    updater = Updater(
        versions_root=root,
        current_version="0.11.6",
        sleep=sleeps.append,
    )

    with pytest.raises(UpdateError) as caught:
        updater.apply_prepared()

    assert caught.value.step == "activate"
    assert "não foi possível trocar a pasta" in caught.value.message.lower()
    assert len(replace_attempts) == 5
    assert [round(s, 2) for s in sleeps] == [0.2, 0.4, 0.6, 0.8]

    attempt = updater.last_attempt()
    assert attempt is not None
    assert attempt["status"] == "failed"
    assert attempt["version"] == "0.12.1"
    assert "não foi possível trocar a pasta" in attempt["message"].lower()
    assert "at" in attempt

    prepared = updater.prepared_release()
    assert prepared is not None
    assert prepared.version == "0.12.1"


def test_write_state_failure_is_logged_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = _setup_installation(tmp_path, current="0.11.6", prepared=None)
    updater = Updater(versions_root=root, current_version="0.11.6")

    def fail_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("disk error writing update-state.json")

    monkeypatch.setattr(updater_module.os, "replace", fail_replace)

    with caplog.at_level(logging.WARNING, logger="orin.update"):
        updater.record_failure("0.12.1", "falha simulada")

    assert any(record.levelno >= logging.WARNING and "state" in record.getMessage() for record in caplog.records)


def test_restart_relaunches_even_on_unexpected_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    root = _setup_installation(tmp_path, current="0.11.6", prepared="0.12.1")
    paths = _paths(tmp_path)
    profile = RuntimeProfile("installed", root / "0.11.6" / "resources" / "runtime", "0.11.6", None)

    def boom(self: Updater) -> Any:
        raise RuntimeError("unexpected failure inside apply_prepared")

    monkeypatch.setattr(Updater, "apply_prepared", boom)
    launched: list[list[str]] = []

    code = cli.command_update(
        paths,
        profile,
        Console(io.StringIO(), colour=False),
        argparse.Namespace(apply=True, restart=True),
        sleep=lambda _: None,
        popen=lambda command, **kw: launched.append(list(command)),
    )

    assert code == 1
    assert len(launched) == 1
    assert launched[0][1:] == ["--desktop"]

    updater = Updater(versions_root=root, current_version="0.11.6")
    attempt = updater.last_attempt()
    assert attempt is not None
    assert attempt["status"] == "failed"
    assert attempt["version"] == "0.12.1"

    update_log = paths.logs / "update.log"
    assert update_log.is_file()
    log_text = update_log.read_text(encoding="utf-8")
    assert "step=relaunch" in log_text


def test_successful_apply_logs_all_steps_to_update_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    root = _setup_installation(tmp_path, current="0.11.6", prepared="0.12.1")
    paths = _paths(tmp_path)
    profile = RuntimeProfile("installed", root / "0.11.6" / "resources" / "runtime", "0.11.6", None)
    monkeypatch.setattr(Updater, "_run_version_check", lambda self, runtime, version, *, step: None)

    launched: list[list[str]] = []
    code = cli.command_update(
        paths,
        profile,
        Console(io.StringIO(), colour=False),
        argparse.Namespace(apply=True, restart=True),
        sleep=lambda _: None,
        popen=lambda command, **kw: launched.append(list(command)),
    )

    assert code == 0
    assert len(launched) == 1
    log_text = (paths.logs / "update.log").read_text(encoding="utf-8")
    for step_name in ("validate", "stop", "activate", "smoke", "state", "relaunch"):
        assert f"step={step_name} outcome=ok duration=" in log_text
