"""Starting Orin in the background when the user signs in to the computer.

Scheduled work (the scheduler and the worker) only runs while Orin does, so
people who rely on it want Orin up without a window in their way. The entry
launches ``orin --desktop --background``: the whole runtime, plus Electron
sitting in the tray with its window hidden.

It points at ``<root>/current`` rather than at a version folder, so an update
never leaves a stale entry behind. Per-user only (HKCU ``Run`` on Windows, an
XDG autostart file on Linux): no administrator rights, nothing machine-wide.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .updater import current_platform, runtime_relative_path

BACKGROUND_ARGUMENTS = ("--desktop", "--background")
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_VALUE = "Orin"


@dataclass(frozen=True, slots=True)
class AutostartStatus:
    supported: bool
    enabled: bool

    def as_dict(self) -> dict[str, bool]:
        return {"supported": self.supported, "enabled": self.enabled}


def windows_command(root: Path) -> str:
    """Hidden, so signing in never flashes a console window."""
    runtime = root / "current" / runtime_relative_path("windows-x64")
    arguments = ",".join(f"'{argument}'" for argument in BACKGROUND_ARGUMENTS)
    return (
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command '
        f'"Start-Process -FilePath \'{runtime}\' -ArgumentList {arguments} -WindowStyle Hidden"'
    )


def autostart_entry(root: Path) -> str:
    runtime = root / "current" / runtime_relative_path("linux-x64")
    return (
        "[Desktop Entry]\nType=Application\nName=Orin\n"
        "Comment=Orin em segundo plano (tarefas agendadas)\n"
        f'Exec="{runtime}" {" ".join(BACKGROUND_ARGUMENTS)}\n'
        "Terminal=false\nX-GNOME-Autostart-enabled=true\n"
    )


def autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "orin.desktop"


class Autostart:
    """``root`` is the installation root; ``None`` means this is not a packaged install."""

    def __init__(self, root: Path | None, *, system: str | None = None) -> None:
        self.root = root
        self.system = system or current_platform()

    @property
    def windows(self) -> bool:
        return self.system.startswith("windows")

    def status(self) -> AutostartStatus:
        if self.root is None:
            return AutostartStatus(supported=False, enabled=False)
        return AutostartStatus(supported=True, enabled=self._enabled())

    def set(self, enabled: bool) -> AutostartStatus:
        if self.root is None:
            raise RuntimeError("Iniciar com o computador só está disponível no Orin instalado.")
        if enabled:
            self._enable(self.root)
        else:
            self._disable()
        return self.status()

    # -- platform specifics -------------------------------------------

    def _enabled(self) -> bool:
        if self.windows:
            import winreg  # type: ignore[import-not-found]

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                    winreg.QueryValueEx(key, WINDOWS_VALUE)
                return True
            except FileNotFoundError:
                return False
        return autostart_file().is_file()

    def _enable(self, root: Path) -> None:
        if self.windows:
            import winreg  # type: ignore[import-not-found]

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                winreg.SetValueEx(key, WINDOWS_VALUE, 0, winreg.REG_SZ, windows_command(root))
            return
        target = autostart_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(autostart_entry(root), encoding="utf-8")

    def _disable(self) -> None:
        if self.windows:
            import winreg  # type: ignore[import-not-found]

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, WINDOWS_VALUE)
            except FileNotFoundError:
                pass
            return
        autostart_file().unlink(missing_ok=True)


def autostart_for(profile) -> Autostart:  # noqa: ANN001 - RuntimeProfile; avoids an import cycle
    from .versions import installation_root

    return Autostart(installation_root(profile))


__all__ = ["Autostart", "AutostartStatus", "autostart_entry", "autostart_file", "autostart_for", "windows_command"]
