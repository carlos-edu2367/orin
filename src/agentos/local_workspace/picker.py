"""The operating system's own folder chooser, run out of process.

The browser cannot hand over an absolute path, so the local server opens the
dialog — the same reasoning that already puts ``os.startfile`` behind a
user-initiated route. It runs in a subprocess with a timeout because a dialog
left open behind another window must never hold an API worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys

DIALOG_TIMEOUT_SECONDS = 180
PROMPT = "Escolha a pasta de trabalho do agente"

_POWERSHELL_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f"$dialog.Description = '{PROMPT}'; "
    "$dialog.ShowNewFolderButton = $true; "
    "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }"
)


@dataclass(frozen=True, slots=True)
class PickResult:
    path: str | None
    cancelled: bool
    available: bool


def dialog_command() -> list[str]:
    if sys.platform.startswith("win"):
        return ["powershell", "-NoProfile", "-STA", "-Command", _POWERSHELL_SCRIPT]
    if sys.platform == "darwin":
        return ["osascript", "-e", f'POSIX path of (choose folder with prompt "{PROMPT}")']
    return ["zenity", "--file-selection", "--directory", f"--title={PROMPT}"]


def fallback_command() -> list[str]:
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return []
    return ["kdialog", "--getexistingdirectory", "."]


def choose_folder(*, command: list[str] | None = None, timeout: int = DIALOG_TIMEOUT_SECONDS) -> PickResult:
    commands = [command] if command is not None else [dialog_command(), fallback_command()]
    unavailable = PickResult(path=None, cancelled=False, available=False)
    for candidate in commands:
        if not candidate:
            continue
        try:
            completed = subprocess.run(candidate, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - fixed platform dialog, no user input in the command
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        selected = completed.stdout.strip()
        if selected:
            return PickResult(path=selected, cancelled=False, available=True)
        return PickResult(path=None, cancelled=True, available=True)
    return unavailable
