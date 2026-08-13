from __future__ import annotations

import json
import io
import sys
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher.cli import build_parser
from agentos.launcher.desktop import _electron_command
from agentos.launcher.desktop_status import DesktopStatusWriter, SERVICE_ORDER
from agentos.launcher import supervisor as supervisor_module
from agentos.launcher.environment import RuntimeEnvironment
from agentos.launcher.supervisor import StartupFailed, Supervisor
from agentos.launcher.ui import Console


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def test_desktop_status_snapshot_is_complete_and_atomic(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    writer = DesktopStatusWriter(paths, restart_command=(sys.executable, "-m", "agentos.launcher"))

    writer.set_url("http://127.0.0.1:49200")
    writer.service("docker", "ready", "Docker Desktop disponível")
    writer.failed("backend", "A API não respondeu")

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert tuple(payload["services"]) == SERVICE_ORDER
    assert payload["mode"] == "error"
    assert payload["url"] == "http://127.0.0.1:49200"
    assert payload["services"]["docker"]["state"] == "ready"
    assert payload["services"]["backend"]["state"] == "error"
    assert not writer.path.with_suffix(".tmp").exists()

    writer.ready("http://127.0.0.1:49200")
    assert json.loads(writer.path.read_text(encoding="utf-8"))["mode"] == "ready"


def test_desktop_flags_are_accepted_without_changing_the_default_command() -> None:
    parser = build_parser()

    normal = parser.parse_args([])
    desktop = parser.parse_args(["--desktop", "--desktop-devtools"])

    assert normal.desktop is False
    assert normal.no_browser is False
    assert desktop.desktop is True
    assert desktop.desktop_devtools is True


def test_development_profile_uses_the_local_electron_package(tmp_path: Path) -> None:
    desktop = tmp_path / "desktop"
    executable = desktop / "node_modules" / ".bin" / ("electron.cmd" if sys.platform == "win32" else "electron")
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    profile = RuntimeProfile("development", tmp_path, "1", tmp_path)

    command = _electron_command(profile)

    assert command == (str(executable), str(desktop))


class _Cp1252Stream(io.StringIO):
    encoding = "cp1252"

    def write(self, value: str) -> int:
        value.encode(self.encoding)
        return super().write(value)


def test_console_degrades_symbols_for_a_legacy_windows_encoding() -> None:
    stream = _Cp1252Stream()

    Console(stream, colour=False).step("Services")

    assert "? Services" in stream.getvalue()


def test_datastore_failure_is_published_to_the_desktop_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    supervisor = Supervisor(
        paths=paths,
        profile=RuntimeProfile("installed", tmp_path / "install", "1", None),
        console=Console(io.StringIO(), colour=False),
    )
    supervisor.environment = RuntimeEnvironment({"DATABASE_URL": "postgresql://x", "REDIS_URL": "redis://x"}, (), None)
    supervisor.desktop_status = DesktopStatusWriter(paths, restart_command=(sys.executable, "-m", "agentos.launcher"))
    monkeypatch.setattr(
        supervisor_module,
        "ensure_datastores",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("Docker Desktop is not running")),
    )

    with pytest.raises(StartupFailed):
        supervisor._step_services()

    assert supervisor.desktop_status.snapshot.mode == "error"
    assert supervisor.desktop_status.snapshot.services["docker"].state == "error"
    assert "Docker Desktop" in supervisor.desktop_status.snapshot.message
