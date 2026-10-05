from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from agentos.installation import installer as inst
from agentos.installation import setup_app
from agentos.installation.installer import InstallOptions, Installer
from agentos.installation.updater import UpdateEvent

from tests.unit.installation.test_updater import BASE, Server, _archive, _installed

posix_only = pytest.mark.skipif(os.name == "nt", reason="the fake runtimes are POSIX shell scripts")


class FakeIntegration:
    def __init__(self, hint: str | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[InstallOptions, str]] = []
        self.hint, self.fail = hint, fail

    def integrate(self, options: InstallOptions, version: str) -> str | None:
        if self.fail:
            raise OSError("boom")
        self.calls.append((options, version))
        return self.hint


def _installer(tmp_path: Path, server: Server, integration: FakeIntegration, events: list | None = None, **options) -> Installer:
    root = tmp_path / "Orin"
    opts = InstallOptions(root=root, bin_root=tmp_path / "bin", **options)
    return Installer(opts, emit=(events if events is not None else []).append, integration=integration, platform="linux-x64",
                     base_url=BASE, opener=server, sleep=lambda _: None)


def test_merge_path_adds_once_and_removes_case_insensitively() -> None:
    assert inst.merge_path(r"C:\a;C:\b", r"C:\Orin\bin") == r"C:\a;C:\b;C:\Orin\bin"
    assert inst.merge_path(r"C:\a;c:\orin\BIN\;C:\b", r"C:\Orin\bin") == r"C:\a;C:\b;C:\Orin\bin"
    assert inst.merge_path(r"C:\a;C:\Orin\bin", r"C:\Orin\bin", remove=True) == r"C:\a"
    assert inst.merge_path("", r"C:\Orin\bin") == r"C:\Orin\bin"


def test_generated_launchers_point_through_current() -> None:
    root = Path("/opt/Orin")
    assert 'exec "/opt/Orin/current/resources/runtime/orin" "$@"' in inst.posix_shim(root)
    assert inst.windows_shim(Path(r"C:\Orin")).startswith("@echo off\r\n")
    assert "current" in inst.windows_shim(Path(r"C:\Orin")) and "orin.exe" in inst.windows_shim(Path(r"C:\Orin"))
    assert "--desktop" in inst.desktop_entry(root)
    entry = inst.uninstall_entry(Path(r"C:\Orin"), "0.5.0")
    assert entry["DisplayVersion"] == "0.5.0" and entry["UninstallString"].endswith("--uninstall") and entry["NoModify"] == 1


def test_shortcut_script_escapes_single_quotes() -> None:
    script = inst.shortcut_script(Path("C:/Users/O'Neil/Desktop/Orin.lnk"), "powershell.exe", "-File x", Path("C:/bin"), Path("C:/i.exe"))
    assert "O''Neil" in script and script.endswith("$s.Save()")


@posix_only
def test_first_install_runs_the_engine_then_integrates(tmp_path: Path) -> None:
    integration = FakeIntegration(hint="adicione ao PATH")
    events: list[UpdateEvent] = []
    result = _installer(tmp_path, Server("0.5.0", _archive("0.5.0"), notes="n"), integration, events).run()

    assert (result.status, result.version, result.path_hint) == ("installed", "0.5.0", "adicione ao PATH")
    assert (tmp_path / "Orin" / "current").resolve() == (tmp_path / "Orin" / "0.5.0").resolve()
    assert integration.calls[0][1] == "0.5.0"
    assert result.launch_command == (str(tmp_path / "Orin" / "current" / "resources" / "runtime" / "orin"), "--desktop")
    started = [e.step for e in events if e.phase == "start"]
    assert started == ["check", "download", "verify", "extract", "validate", "activate", "integrate"]


@posix_only
def test_installing_over_an_existing_install_updates_and_repairs(tmp_path: Path) -> None:
    root = _installed(tmp_path)  # creates <tmp>/Orin with 0.4.0 active
    integration = FakeIntegration()
    installer = Installer(InstallOptions(root=root, bin_root=tmp_path / "bin"), integration=integration, platform="linux-x64",
                          base_url=BASE, opener=Server("0.5.0", _archive("0.5.0")), sleep=lambda _: None)

    result = installer.run()
    assert (result.status, result.version) == ("updated", "0.5.0")

    again = Installer(InstallOptions(root=root, bin_root=tmp_path / "bin"), integration=integration, platform="linux-x64",
                      base_url=BASE, opener=Server("0.5.0", _archive("0.5.0")), sleep=lambda _: None).run()
    assert (again.status, again.version) == ("up_to_date", "0.5.0")
    assert len(integration.calls) == 2  # shortcuts are repaired even when nothing new was downloaded


@posix_only
def test_a_failed_download_installs_nothing_and_skips_integration(tmp_path: Path) -> None:
    integration = FakeIntegration()
    from agentos.installation.updater import UpdateError
    with pytest.raises(UpdateError, match="assinatura"):
        _installer(tmp_path, Server("0.5.0", _archive("0.5.0"), sha="0" * 64), integration).run()
    assert integration.calls == [] and not (tmp_path / "Orin" / "current").exists()


@posix_only
def test_integration_failure_is_reported_as_installed_but_incomplete(tmp_path: Path) -> None:
    from agentos.installation.updater import UpdateError
    with pytest.raises(UpdateError, match="foi instalado, mas") as caught:
        _installer(tmp_path, Server("0.5.0", _archive("0.5.0")), FakeIntegration(fail=True)).run()
    assert caught.value.step == "integrate" and (tmp_path / "Orin" / "current").exists()


@posix_only
def test_posix_integration_writes_the_command_and_a_menu_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", "/usr/bin")
    options = InstallOptions(root=tmp_path / "Orin", bin_root=tmp_path / "bin")

    hint = inst.PosixIntegration().integrate(options, "0.5.0")

    shim = tmp_path / "bin" / "orin"
    assert os.access(shim, os.X_OK) and "current/resources/runtime/orin" in shim.read_text()
    assert (tmp_path / "home" / ".local" / "share" / "applications" / "orin-desktop.desktop").is_file()
    assert hint is not None and str(tmp_path / "bin") in hint


def test_model_tracks_steps_and_overall_progress() -> None:
    model = setup_app.SetupModel()
    assert [s.state for s in model.steps] == ["pending"] * 7 and model.fraction == 0
    model.apply(UpdateEvent("start", "check", 0, 6, "Procurando"))
    model.apply(UpdateEvent("done", "check", 0, 6, "Procurando", detail="versão 0.5.0"))
    model.apply(UpdateEvent("start", "download", 1, 6, "Baixando a versão 0.5.0"))
    model.apply(UpdateEvent("progress", "download", 1, 6, "Baixando", progress=0.5, bytes_done=75_000_000, bytes_total=150_000_000, speed=8_000_000))

    assert [s.state for s in model.steps][:3] == ["done", "running", "pending"]
    assert model.steps[0].detail == "versão 0.5.0" and model.steps[1].label == "Baixando a versão 0.5.0"
    assert 0.3 < model.fraction < 0.4
    assert "de" in model.detail and "MB/s" in model.detail and "~" in model.detail

    model.fail()
    assert model.steps[1].state == "failed" and model.detail == ""


def test_model_reaches_one_when_every_step_is_done() -> None:
    model = setup_app.SetupModel()
    for key, _ in setup_app.SETUP_STEPS:
        model.apply(UpdateEvent("start", key, 0, 7, key))
        model.apply(UpdateEvent("done", key, 0, 7, key))
    assert model.fraction == pytest.approx(1.0)


def test_options_from_arguments() -> None:
    arguments = setup_app.build_parser().parse_args(["--silent", "--dir", "/x/Orin", "--to", "0.5.0", "--no-shortcut", "--no-path"])
    options = setup_app.options_from(arguments)
    assert (options.root, options.version, options.desktop_shortcut, options.add_to_path) == (Path("/x/Orin"), "0.5.0", False, False)


@posix_only
def test_silent_mode_prints_numbered_steps_and_a_summary(tmp_path: Path) -> None:
    out = io.StringIO()
    integration = FakeIntegration(hint="export PATH=...")

    def factory(options, emit=None):
        return Installer(options, emit=emit, integration=integration, platform="linux-x64", base_url=BASE,
                         opener=Server("0.5.0", _archive("0.5.0")), sleep=lambda _: None)

    code = setup_app.run_silent(InstallOptions(root=tmp_path / "Orin", bin_root=tmp_path / "bin"), stream=out, installer_factory=factory)

    text = out.getvalue()
    assert code == 0 and "Orin instalado" in text and "0.5.0" in text and "export PATH" in text
    assert "[1/7]" in text and "[7/7]" in text


@posix_only
def test_silent_mode_failure_exits_nonzero_with_the_reason(tmp_path: Path) -> None:
    out = io.StringIO()

    def factory(options, emit=None):
        return Installer(options, emit=emit, integration=FakeIntegration(), platform="linux-x64", base_url=BASE,
                         opener=Server("0.5.0", _archive("0.5.0"), sha="0" * 64), sleep=lambda _: None)

    assert setup_app.run_silent(InstallOptions(root=tmp_path / "Orin", bin_root=tmp_path / "bin"), stream=out, installer_factory=factory) == 1
    assert "não foi concluída" in out.getvalue()


def test_setup_help_exits_cleanly_and_describes_the_silent_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        setup_app.main(["--help"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert "--silent" in out and "--no-shortcut" in out and "--to" in out


@posix_only
def test_posix_integration_remove_deletes_the_command_and_menu_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("PATH", "/usr/bin")
    options = InstallOptions(root=tmp_path / "Orin", bin_root=tmp_path / "bin")
    integration = inst.PosixIntegration()
    integration.integrate(options, "0.5.0")
    assert (tmp_path / "bin" / "orin").exists()

    integration.remove(options)

    assert not (tmp_path / "bin" / "orin").exists()
    assert not (tmp_path / "home" / ".local" / "share" / "applications" / "orin-desktop.desktop").exists()
    integration.remove(options)  # removing twice is harmless
