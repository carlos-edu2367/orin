from __future__ import annotations

import io
import argparse
from pathlib import Path

import pytest

from agentos.installation import paths as paths_module, versions
from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import cli
from agentos.launcher.ui import Console


class FakeIntegration:
    def __init__(self) -> None:
        self.removed: list[object] = []

    def integrate(self, options, version):  # pragma: no cover - unused
        return None

    def remove(self, options) -> None:
        self.removed.append(options)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A frozen-looking install under ``…/Orin/versions`` with Orin-named state beside it."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths_module, "_user_state_root", lambda: home / ".local" / "share" / "orin")
    monkeypatch.setattr(paths_module, "_user_config_root", lambda: home / ".config" / "orin")
    from agentos.installation import uninstaller
    monkeypatch.setattr(uninstaller, "_user_state_root", lambda: home / ".local" / "share" / "orin")
    monkeypatch.setattr(uninstaller, "_user_config_root", lambda: home / ".config" / "orin")
    root = home / ".local" / "share" / "Orin" / "versions"
    runtime = root / "1.0.0" / "resources" / "runtime"
    runtime.mkdir(parents=True)
    state, config = home / ".local" / "share" / "orin", home / ".config" / "orin"
    for directory in (state / "data", state / "logs", config):
        directory.mkdir(parents=True)
    paths = OrinPaths(config, state / "data", state / "logs", state / "cache", state / "run")
    return RuntimeProfile("installed", runtime, "1.0.0", None), paths, root


def _run(layout, *, answer: str | None = None, yes: bool = False, tty: bool = True, monkeypatch=None):
    profile, paths, _root = layout
    stream, spawned = io.StringIO(), []
    code = cli.command_uninstall(
        paths, profile, Console(stream, colour=False), argparse.Namespace(yes=yes),
        ask=lambda prompt: answer or "", popen=lambda command, **kw: spawned.append((command, kw)),
    )
    return code, stream.getvalue(), spawned


def test_uninstall_lists_what_goes_asks_and_schedules_the_removal(layout, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import installer
    fake = FakeIntegration()
    monkeypatch.setattr("agentos.installation.uninstaller.integration_for", lambda system=None: fake)
    monkeypatch.setattr(cli.sys, "stdin", type("T", (), {"isatty": lambda self: True})())

    code, out, spawned = _run(layout, answer="s")

    assert code == 0
    assert "conversas, memórias" in out and "Comando e atalhos removidos" in out
    assert len(fake.removed) == 1
    command, kwargs = spawned[0]
    assert command[0] == "sh" and any(str(layout[2].parent) == part for part in command)  # …/Orin, not …/Orin/versions
    assert str(layout[1].data.parent) in command  # …/share/orin


def test_declining_removes_nothing(layout, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeIntegration()
    monkeypatch.setattr("agentos.installation.uninstaller.integration_for", lambda system=None: fake)
    monkeypatch.setattr(cli.sys, "stdin", type("T", (), {"isatty": lambda self: True})())

    code, out, spawned = _run(layout, answer="n")

    assert code == 0 and "Nada foi removido" in out and fake.removed == [] and spawned == []


def test_without_a_terminal_it_refuses_unless_yes_is_passed(layout, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeIntegration()
    monkeypatch.setattr("agentos.installation.uninstaller.integration_for", lambda system=None: fake)
    monkeypatch.setattr(cli.sys, "stdin", type("T", (), {"isatty": lambda self: False})())

    code, out, spawned = _run(layout)
    assert code == 2 and "--yes" in out and spawned == []

    code, _out, spawned = _run(layout, yes=True)
    assert code == 0 and len(spawned) == 1


def test_uninstall_refuses_to_delete_a_source_checkout(tmp_path: Path) -> None:
    stream = io.StringIO()
    profile = RuntimeProfile("development", tmp_path, "1.0.0", tmp_path)
    paths = OrinPaths(tmp_path / "c", tmp_path / "d", tmp_path / "l", tmp_path / "ca", tmp_path / "r").ensure()

    assert cli.command_uninstall(paths, profile, Console(stream, colour=False)) == 2
    assert "will not delete this source checkout" in stream.getvalue()


def test_plan_refuses_a_directory_that_is_not_orin_named(tmp_path: Path) -> None:
    from agentos.installation.uninstaller import plan_uninstall
    from agentos.installation.updater import UpdateError

    paths = OrinPaths(tmp_path / "c", tmp_path / "d", tmp_path / "l", tmp_path / "ca", tmp_path / "r")
    with pytest.raises(UpdateError, match="Por segurança"):
        plan_uninstall(tmp_path / "Documents", paths)
    with pytest.raises(UpdateError, match="Por segurança"):
        plan_uninstall(Path.home(), paths)


def test_a_custom_data_directory_is_reported_and_kept(layout, tmp_path: Path) -> None:
    from agentos.installation.uninstaller import plan_uninstall

    _profile, paths, root = layout
    elsewhere = OrinPaths(paths.config, tmp_path / "elsewhere" / "data", paths.logs, paths.cache, paths.run)

    plan = plan_uninstall(root, elsewhere)

    assert (tmp_path / "elsewhere" / "data") in plan.kept
    assert all("elsewhere" not in str(target) for target in plan.targets)


def test_deferred_removal_passes_paths_as_data_not_script_text(tmp_path: Path) -> None:
    from agentos.installation.uninstaller import deferred_removal_command

    nasty = Path("/tmp/it's a \"folder\"; rm -rf ~")
    command, env = deferred_removal_command((nasty,), 4242, "linux-x64")
    assert command[0] == "sh" and str(nasty) in command and "4242" in command and nasty.name not in command[2]

    command, env = deferred_removal_command((Path(r"C:\Users\x\Orin"), Path(r"C:\Users\x\AppData\Local\Orin")), 77, "windows-x64")
    assert command[0] == "powershell.exe" and env["ORIN_REMOVE_PID"] == "77"
    assert env["ORIN_REMOVE_PATHS"] == r"C:\Users\x\Orin|C:\Users\x\AppData\Local\Orin" and "Users" not in command[-1]


@pytest.mark.skipif(__import__("os").name == "nt", reason="exercises the POSIX helper")
def test_the_helper_really_waits_for_the_pid_and_then_deletes_everything(tmp_path: Path) -> None:
    import subprocess
    import time
    from agentos.installation.uninstaller import deferred_removal_command

    victim = tmp_path / "it's \"Orin\" dir; $(touch pwned)"
    (victim / "nested").mkdir(parents=True)
    (victim / "nested" / "file").write_text("x")
    other = tmp_path / "orin"
    other.mkdir()
    import threading
    parent = subprocess.Popen(["sleep", "1.2"])
    threading.Thread(target=parent.wait, daemon=True).start()  # reaped, like a real parent shell would
    command, env = deferred_removal_command((victim, other), parent.pid, "linux-x64")

    helper = subprocess.Popen(command)
    time.sleep(0.4)
    assert victim.exists(), "must wait for the process to exit before deleting"
    assert helper.wait(timeout=15) == 0
    assert not victim.exists() and not other.exists() and not (tmp_path / "pwned").exists()
