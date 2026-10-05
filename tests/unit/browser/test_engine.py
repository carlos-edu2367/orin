import sys
import time

from agentos.browser import engine
from agentos.browser.engine import BrowserEngineInstaller, agent_install_guidance, chromium_installed


def _fake_install(root, *, code=0, mark=True):
    script = (
        "import pathlib,sys,time\n"
        "print('Downloading Chromium 1.0', flush=True)\n"
        "for n in (10, 55, 100):\n"
        "    sys.stdout.write(f'|■■■| {n}% of 150 MiB\\r'); sys.stdout.flush()\n"
        f"d = pathlib.Path({str(root)!r}) / 'chromium-1234'; d.mkdir(parents=True, exist_ok=True)\n"
        f"{'(d / \"INSTALLATION_COMPLETE\").write_text(\"\")' if mark else 'pass'}\n"
        f"sys.exit({code})\n"
    )
    return lambda: [sys.executable, "-c", script]


def _wait(installer, timeout=10):
    deadline = time.time() + timeout
    while installer.status().state == "installing" and time.time() < deadline:
        time.sleep(0.05)
    return installer.status()


def test_chromium_installed_requires_the_completion_marker(tmp_path):
    (tmp_path / "chromium-1").mkdir()
    assert not chromium_installed(tmp_path)
    (tmp_path / "chromium-1" / "INSTALLATION_COMPLETE").write_text("")
    assert chromium_installed(tmp_path)
    assert not chromium_installed(tmp_path / "missing")


def test_install_reports_progress_and_finishes_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    installer = BrowserEngineInstaller(command=_fake_install(tmp_path), environment=lambda: {})
    assert installer.status().state == "missing"
    first = installer.start()
    assert first.state == "installing"
    assert _wait(installer).state == "ready"


def test_a_failed_install_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    installer = BrowserEngineInstaller(command=_fake_install(tmp_path, code=1, mark=False), environment=lambda: {})
    installer.start()
    final = _wait(installer)
    assert final.state == "failed"
    assert "código 1" in (final.error or "")
    assert final.as_dict()["install_command"] == "orin browser install"


def test_agent_guidance_names_both_ways_to_install():
    text = agent_install_guidance()
    assert "Configurações > Navegador" in text and "orin browser install" in text


def test_missing_engine_is_detected_only_when_the_package_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(engine, "playwright_package_present", lambda: True)
    assert engine.engine_missing_for_agent()
    monkeypatch.setattr(engine, "playwright_package_present", lambda: False)
    assert not engine.engine_missing_for_agent()


def test_unset_variable_falls_back_to_playwrights_own_cache(tmp_path, monkeypatch):
    # A checkout (or CI) never exports PLAYWRIGHT_BROWSERS_PATH; the browser then
    # lives where Playwright put it, and must still count as installed.
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine.os, "name", "posix")
    default = tmp_path / "ms-playwright"
    assert engine.browsers_path() == default and not chromium_installed()
    (default / "chromium_headless_shell-1181").mkdir(parents=True)
    (default / "chromium_headless_shell-1181" / "INSTALLATION_COMPLETE").write_text("")
    assert chromium_installed()
