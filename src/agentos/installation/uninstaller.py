"""Removing an installed Orin: the counterpart of ``installer.py``.

Everything here is conservative on purpose. It deletes the installation, the
per-user state that belongs to Orin (data, configuration, logs) and the command
and shortcuts the installer created, and nothing else: every path is checked to
be an Orin-named directory that is not a source checkout, and a custom data
directory somebody pointed Orin at is reported and left alone.

The running ``orin`` cannot delete the folder it is running from (Windows locks
it), so the deletion itself is handed to a tiny detached helper that waits for
this process to exit.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Callable

from .autostart import Autostart
from .installer import InstallOptions, default_bin_root, integration_for
from .paths import OrinPaths, _user_config_root, _user_state_root
from .updater import UpdateError, current_platform


@dataclass(frozen=True, slots=True)
class UninstallPlan:
    versions_root: Path
    """Where the versioned installation lives (``…/Orin`` or ``…/Orin/versions``)."""
    targets: tuple[Path, ...]
    """Directories that will be deleted, existing ones only."""
    kept: tuple[Path, ...] = ()
    """Orin directories outside the standard locations, which are left alone."""
    bin_root: Path | None = None


def _is_orin_directory(path: Path) -> bool:
    return path.name.lower() == "orin" and path != Path(path.anchor) and path != Path.home()


def plan_uninstall(versions_root: Path, paths: OrinPaths, *, system: str | None = None) -> UninstallPlan:
    """What would be removed. Raises ``UpdateError`` if the layout looks wrong."""
    system = system or current_platform()
    root = versions_root.resolve()
    # ``…/Orin/versions`` (Linux) removes its ``Orin`` parent; ``…\Programs\Orin`` (Windows) is itself.
    install_dir = root if root.name.lower() == "orin" else root.parent if root.parent.name.lower() == "orin" else None
    if install_dir is None or not _is_orin_directory(install_dir):
        raise UpdateError(
            f"Por segurança não removo {root}: não parece uma pasta de instalação do Orin.",
            step="uninstall", hint="Remova a pasta manualmente se tiver certeza.",
        )
    state_roots = [candidate for candidate in {_user_state_root(), _user_config_root()} if _is_orin_directory(candidate)]
    targets = [install_dir, *state_roots]
    kept = tuple(
        directory for directory in (paths.config, paths.data, paths.logs, paths.cache, paths.run)
        if not any(directory.resolve().is_relative_to(target.resolve()) for target in targets)
    )
    existing = tuple(dict.fromkeys(target for target in targets if target.exists()))
    return UninstallPlan(install_dir, existing, kept, default_bin_root(system))


def deferred_removal_command(targets: tuple[Path, ...], wait_for_pid: int, system: str | None = None) -> tuple[list[str], dict[str, str]]:
    """The helper that deletes ``targets`` once ``wait_for_pid`` has exited.

    Paths travel through the environment (and argv on POSIX), never spliced into
    script text, so an unusual folder name cannot become code.
    """
    system = system or current_platform()
    if system.startswith("windows"):
        script = (
            "$ErrorActionPreference='SilentlyContinue'; "
            "$p=[int]$env:ORIN_REMOVE_PID; if ($p -gt 0) { Wait-Process -Id $p }; "
            "$t=$env:ORIN_REMOVE_PATHS -split '\\|'; "
            "for ($i=0; $i -lt 180; $i++) { foreach ($x in $t) { Remove-Item -LiteralPath $x -Recurse -Force }; "
            "if (-not ($t | Where-Object { Test-Path -LiteralPath $_ })) { exit 0 }; Start-Sleep -Seconds 1 }; exit 1"
        )
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script]
        return command, {"ORIN_REMOVE_PID": str(wait_for_pid), "ORIN_REMOVE_PATHS": "|".join(str(t) for t in targets)}
    script = (
        'pid="$1"; shift; '
        'if [ "$pid" -gt 0 ] 2>/dev/null; then while kill -0 "$pid" 2>/dev/null; do sleep 1; done; fi; '
        'i=0; while [ "$i" -lt 180 ]; do rm -rf -- "$@"; gone=1; '
        'for p in "$@"; do [ -e "$p" ] && gone=0; done; [ "$gone" = 1 ] && exit 0; i=$((i+1)); sleep 1; done; exit 1'
    )
    return ["sh", "-c", script, "orin-remove", str(wait_for_pid), *[str(t) for t in targets]], {}


def uninstall(
    plan: UninstallPlan,
    *,
    wait_for_pid: int | None = None,
    system: str | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    integration=None,
) -> None:
    """Take the command, shortcuts and registry entry away now; schedule the folders."""
    system = system or current_platform()
    options = InstallOptions(root=plan.versions_root, bin_root=plan.bin_root or default_bin_root(system))
    try:
        (integration or integration_for(system)).remove(options)
    except Exception as error:  # noqa: BLE001 - a half-removed shortcut must not block removing the files
        raise UpdateError(
            f"Não consegui remover o comando e os atalhos ({type(error).__name__}).",
            step="uninstall", hint="Feche o Orin e tente de novo.",
        ) from None
    try:
        Autostart(plan.versions_root, system=system).set(False)
    except Exception:  # noqa: BLE001 - a leftover login entry only points at a folder that is going away
        pass
    command, extra_env = deferred_removal_command(plan.targets, wait_for_pid if wait_for_pid is not None else os.getpid(), system)
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        popen(
            command, env={**os.environ, **extra_env}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=os.name != "nt", creationflags=flags,
        )
    except OSError as error:
        raise UpdateError(
            f"Não consegui agendar a remoção das pastas ({type(error).__name__}).",
            step="uninstall", hint="O comando e os atalhos já foram removidos; apague as pastas manualmente.",
        ) from None


__all__ = ["UninstallPlan", "deferred_removal_command", "plan_uninstall", "uninstall"]
