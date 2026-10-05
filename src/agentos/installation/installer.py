"""First-time installation: the same engine as ``orin update``, plus the parts only
a first install needs (the ``orin`` command, shortcuts, an "Apps & features" entry).

``OrinSetup`` (the graphical installer) and ``OrinSetup --silent`` both run this.
It deliberately reuses ``Updater`` for everything that moves bytes (download,
SHA-256, staged extraction, smoke tests, rollback), so a first install gets the
exact safety properties of an update, and installing over an existing Orin simply
*is* an update that also repairs the shortcuts.

Locations match ``install.ps1`` / ``install.sh`` so an installation made by
either one is recognised by the other, and by ``orin --uninstall``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import subprocess
from typing import Callable, Protocol

from .updater import STEPS, UpdateError, UpdateEvent, UpdateResult, Updater, current_platform, runtime_relative_path

#: The final step the installer adds after the engine's own.
INTEGRATE_STEP = ("integrate", "Criando o comando orin e os atalhos")
SETUP_STEPS: tuple[tuple[str, str], ...] = (*STEPS, INTEGRATE_STEP)
FIRST_INSTALL_VERSION = "0.0.0"


def default_install_root(system: str | None = None) -> Path:
    system = system or current_platform()
    if system.startswith("windows"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Programs" / "Orin"
    return Path.home() / ".local" / "share" / "Orin" / "versions"


def default_bin_root(system: str | None = None) -> Path:
    system = system or current_platform()
    if system.startswith("windows"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Orin" / "bin"
    return Path.home() / ".local" / "bin"


@dataclass(frozen=True, slots=True)
class InstallOptions:
    root: Path
    bin_root: Path
    version: str = "latest"
    desktop_shortcut: bool = True
    menu_entry: bool = True
    add_to_path: bool = True

    @classmethod
    def defaults(cls, system: str | None = None, **overrides: object) -> "InstallOptions":
        values: dict[str, object] = {"root": default_install_root(system), "bin_root": default_bin_root(system)}
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class InstallResult:
    status: str  # "installed" | "updated" | "up_to_date"
    version: str
    root: Path
    launch_command: tuple[str, ...]
    path_hint: str | None = None
    release_url: str | None = None
    notes: str | None = None
    downloaded_bytes: int = 0
    seconds: float = 0.0


class Integration(Protocol):
    """What "installed" means on one operating system, beyond the files themselves."""

    def integrate(self, options: InstallOptions, version: str) -> str | None:
        """Create the command/shortcuts. Returns a hint to show the person, if any."""

    def remove(self, options: InstallOptions) -> None:
        """Undo ``integrate``: the command, shortcuts, PATH entry and registry entry."""


# -- pure helpers (tested on every platform) ------------------------------


def merge_path(existing: str, directory: str, *, remove: bool = False, separator: str = ";") -> str:
    """``existing`` with ``directory`` appended once (or removed), compared case-insensitively."""
    wanted = directory.rstrip("\\/").lower()
    kept = [entry for entry in existing.split(separator) if entry and entry.rstrip("\\/").lower() != wanted]
    if not remove:
        kept.append(directory)
    return separator.join(kept)


def windows_shim(root: Path) -> str:
    return f'@echo off\r\n"{root / "current" / runtime_relative_path("windows-x64")}" %*\r\n'


def windows_desktop_launcher(root: Path) -> str:
    runtime = root / "current" / runtime_relative_path("windows-x64")
    return f"Start-Process -FilePath '{runtime}' -ArgumentList '--desktop' -WindowStyle Hidden\r\n"


def posix_shim(root: Path) -> str:
    return f'#!/usr/bin/env bash\nexec "{root / "current" / runtime_relative_path("linux-x64")}" "$@"\n'


def desktop_entry(root: Path) -> str:
    runtime = root / "current" / runtime_relative_path("linux-x64")
    icon = root / "current" / "resources" / "runtime" / "_internal" / "web" / "orin-logo.png"
    return (
        "[Desktop Entry]\nType=Application\nName=Orin Desktop\n"
        f'Exec="{runtime}" --desktop\nIcon={icon}\nTerminal=false\nCategories=Development;\n'
    )


def shortcut_script(link: Path, target: str, arguments: str, working_directory: Path, icon: Path) -> str:
    """PowerShell that writes one ``.lnk`` (no extra dependency on the target machine)."""
    def q(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    return (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + q(link) + "); "
        f"$s.TargetPath = {q(target)}; $s.Arguments = {q(arguments)}; $s.WorkingDirectory = {q(working_directory)}; "
        f"$s.IconLocation = {q(icon)}; $s.Description = 'Orin'; $s.Save()"
    )


def uninstall_entry(root: Path, version: str) -> dict[str, object]:
    """The values of the "Apps & features" registry entry (``HKCU\\...\\Uninstall\\Orin``)."""
    runtime = root / "current" / runtime_relative_path("windows-x64")
    return {
        "DisplayName": "Orin", "DisplayVersion": version, "Publisher": "Orin",
        "InstallLocation": str(root), "DisplayIcon": str(runtime),
        "UninstallString": f'"{runtime}" --uninstall', "NoModify": 1, "NoRepair": 1,
    }


# -- operating-system integrations ---------------------------------------


class WindowsIntegration:
    def integrate(self, options: InstallOptions, version: str) -> str | None:
        import winreg  # type: ignore[import-not-found]

        options.bin_root.mkdir(parents=True, exist_ok=True)
        (options.bin_root / "orin.cmd").write_text(windows_shim(options.root), encoding="ascii")
        launcher = options.bin_root / "orin-desktop.ps1"
        launcher.write_text(windows_desktop_launcher(options.root), encoding="ascii")
        if options.add_to_path:
            self._add_to_user_path(str(options.bin_root))
        icon = options.root / "current" / "Orin Desktop.exe"
        target = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        arguments = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{launcher}"'
        if options.desktop_shortcut:
            self._shortcut(Path(self._known_folder("Desktop")) / "Orin Desktop.lnk", target, arguments, options.bin_root, icon)
        if options.menu_entry:
            programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            programs.mkdir(parents=True, exist_ok=True)
            self._shortcut(programs / "Orin.lnk", target, arguments, options.bin_root, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Orin") as key:
            for name, value in uninstall_entry(options.root, version).items():
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ, value)
        return None

    def remove(self, options: InstallOptions) -> None:
        import winreg  # type: ignore[import-not-found]

        for name in ("orin.cmd", "orin-desktop.ps1", "orin-desktop.vbs"):
            (options.bin_root / name).unlink(missing_ok=True)
        (Path(self._known_folder("Desktop")) / "Orin Desktop.lnk").unlink(missing_ok=True)
        (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Orin.lnk").unlink(missing_ok=True)
        self._add_to_user_path(str(options.bin_root), remove=True)
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Orin")
        except FileNotFoundError:
            pass

    @staticmethod
    def _known_folder(name: str) -> str:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
            capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip() or str(Path.home() / name)

    @staticmethod
    def _shortcut(link: Path, target: str, arguments: str, working_directory: Path, icon: Path) -> None:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", shortcut_script(link, target, arguments, working_directory, icon)],
            check=True, capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _add_to_user_path(directory: str, *, remove: bool = False) -> None:
        import ctypes
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                existing, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                existing, kind = "", winreg.REG_EXPAND_SZ
            updated = merge_path(existing, directory, remove=remove)
            if updated != existing:
                winreg.SetValueEx(key, "Path", 0, kind, updated)
        # Tell running programs (Explorer, new terminals) the environment changed.
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)  # type: ignore[attr-defined]


class PosixIntegration:
    def integrate(self, options: InstallOptions, version: str) -> str | None:
        options.bin_root.mkdir(parents=True, exist_ok=True)
        shim = options.bin_root / "orin"
        shim.write_text(posix_shim(options.root), encoding="utf-8")
        shim.chmod(0o755)
        if options.desktop_shortcut or options.menu_entry:
            applications = Path.home() / ".local" / "share" / "applications"
            applications.mkdir(parents=True, exist_ok=True)
            (applications / "orin-desktop.desktop").write_text(desktop_entry(options.root), encoding="utf-8")
        if str(options.bin_root) not in os.environ.get("PATH", "").split(os.pathsep):
            return f'Para usar o comando orin, adicione ao seu shell: export PATH="{options.bin_root}:$PATH"'
        return None


    def remove(self, options: InstallOptions) -> None:
        (options.bin_root / "orin").unlink(missing_ok=True)
        (Path.home() / ".local" / "share" / "applications" / "orin-desktop.desktop").unlink(missing_ok=True)


def integration_for(system: str | None = None) -> Integration:
    return WindowsIntegration() if (system or current_platform()).startswith("windows") else PosixIntegration()


# -- the installer ---------------------------------------------------------


class Installer:
    def __init__(
        self,
        options: InstallOptions,
        *,
        emit: Callable[[UpdateEvent], None] | None = None,
        integration: Integration | None = None,
        updater_factory: Callable[..., Updater] = Updater,
        platform: str | None = None,
        **updater_options: object,
    ) -> None:
        self.options = options
        self._emit = emit or (lambda event: None)
        self._platform = platform or current_platform()
        self._integration = integration or integration_for(self._platform)
        self._updater_options = updater_options
        self._factory = updater_factory

    def _updater(self, current_version: str) -> Updater:
        # The engine counts its own six steps; the installer adds a seventh.
        return self._factory(
            versions_root=self.options.root, current_version=current_version,
            emit=lambda event: self._emit(replace(event, total=len(SETUP_STEPS))),
            platform=self._platform, **self._updater_options,
        )

    def check(self) -> UpdateResult:
        """The release this would install, without touching disk (for the welcome screen)."""
        probe = self._updater(FIRST_INSTALL_VERSION)
        release = probe.check(self.options.version)
        return UpdateResult("available", FIRST_INSTALL_VERSION, release.version, release.release_url, release.notes)

    def run(self) -> InstallResult:
        existing = self._updater(FIRST_INSTALL_VERSION).active_version()
        updater = self._updater(existing or FIRST_INSTALL_VERSION)
        result = updater.run(self.options.version)
        version = result.version if result.status == "updated" else (existing or result.version)
        index = len(SETUP_STEPS) - 1
        label = INTEGRATE_STEP[1]
        self._emit(UpdateEvent("start", "integrate", index, len(SETUP_STEPS), label))
        try:
            hint = self._integration.integrate(self.options, version)
        except UpdateError:
            raise
        except Exception as error:  # noqa: BLE001 - surfaced with a person-sized message
            raise UpdateError(
                f"O Orin foi instalado, mas não consegui criar o comando e os atalhos ({type(error).__name__}).",
                step="integrate", hint="Você ainda pode abrir o Orin pela pasta de instalação. Rode o instalador de novo para tentar outra vez.",
            ) from None
        self._emit(UpdateEvent("done", "integrate", index, len(SETUP_STEPS), label))
        runtime = self.options.root / "current" / runtime_relative_path(self._platform)
        status = "installed" if existing is None and result.status == "updated" else ("updated" if result.status == "updated" else "up_to_date")
        return InstallResult(
            status, version, self.options.root, (str(runtime), "--desktop"), hint, result.release_url, result.notes,
            result.downloaded_bytes, result.seconds,
        )


__all__ = [
    "INTEGRATE_STEP", "Installer", "InstallOptions", "InstallResult", "SETUP_STEPS", "default_bin_root",
    "default_install_root", "desktop_entry", "merge_path", "posix_shim", "shortcut_script", "uninstall_entry",
    "windows_desktop_launcher", "windows_shim",
]
