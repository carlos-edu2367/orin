"""The operating system's own folder chooser, run out of process.

The browser cannot hand over an absolute path, so the local server opens the
dialog — the same reasoning that already puts ``os.startfile`` behind a
user-initiated route. It runs in a subprocess with a timeout because a dialog
left open behind another window must never hold an API worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from threading import Thread


def _choose_windows_folder() -> PickResult | None:
    """Open the modern Windows Shell folder picker through IFileOpenDialog."""
    if not sys.platform.startswith("win"):
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        hresult = ctypes.c_long
        ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
        ole32.CLSIDFromString.restype = hresult
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = hresult
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = hresult
        ole32.CoUninitialize.argtypes = []
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        clsid = GUID()
        iid = GUID()
        if ole32.CLSIDFromString("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}", ctypes.byref(clsid)) != 0:
            return None
        if ole32.CLSIDFromString("{D57C7288-D4AD-4768-BE02-9D969532D960}", ctypes.byref(iid)) != 0:
            return None

        result: list[PickResult | None] = [None]

        def com_method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
            vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

        def open_dialog() -> None:
            initialized = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            if initialized not in (0, 1):  # S_OK / S_FALSE; use no legacy dialog on COM failure.
                result[0] = PickResult(path=None, cancelled=False, available=False)
                return
            dialog_pointer = ctypes.c_void_p()
            try:
                created = ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1, ctypes.byref(iid), ctypes.byref(dialog_pointer))
                if created != 0 or not dialog_pointer.value:
                    result[0] = PickResult(path=None, cancelled=False, available=False)
                    return

                get_options = com_method(dialog_pointer, 10, hresult, ctypes.POINTER(wintypes.DWORD))
                set_options = com_method(dialog_pointer, 9, hresult, wintypes.DWORD)
                set_title = com_method(dialog_pointer, 17, hresult, wintypes.LPCWSTR)
                show = com_method(dialog_pointer, 3, hresult, wintypes.HWND)
                get_result = com_method(dialog_pointer, 20, hresult, ctypes.POINTER(ctypes.c_void_p))
                release = com_method(dialog_pointer, 2, wintypes.ULONG)

                options = wintypes.DWORD()
                options_result = get_options(dialog_pointer, ctypes.byref(options))
                if options_result != 0:
                    result[0] = PickResult(path=None, cancelled=False, available=False)
                    return
                # FOS_PICKFOLDERS (0x20) gives the modern folder/library view;
                # FORCEFILESYSTEM and PATHMUSTEXIST keep the returned path safe.
                if set_options(dialog_pointer, options.value | 0x20 | 0x40 | 0x800) != 0:
                    result[0] = PickResult(path=None, cancelled=False, available=False)
                    return
                set_title(dialog_pointer, PROMPT)
                shown = show(dialog_pointer, None)
                if shown != 0:
                    result[0] = PickResult(path=None, cancelled=True, available=True)
                    return

                item_pointer = ctypes.c_void_p()
                if get_result(dialog_pointer, ctypes.byref(item_pointer)) != 0 or not item_pointer.value:
                    result[0] = PickResult(path=None, cancelled=True, available=True)
                    return
                try:
                    get_display_name = com_method(item_pointer, 5, hresult, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
                    display_name = wintypes.LPWSTR()
                    if get_display_name(item_pointer, 0x80058000, ctypes.byref(display_name)) != 0 or not display_name.value:
                        result[0] = PickResult(path=None, cancelled=True, available=True)
                    else:
                        result[0] = PickResult(path=display_name.value, cancelled=False, available=True)
                    if display_name.value:
                        ole32.CoTaskMemFree(ctypes.cast(display_name, ctypes.c_void_p))
                finally:
                    release = com_method(item_pointer, 2, wintypes.ULONG)
                    release(item_pointer)
            except (AttributeError, OSError, TypeError, ValueError):
                result[0] = PickResult(path=None, cancelled=False, available=False)
            finally:
                if dialog_pointer.value:
                    release = com_method(dialog_pointer, 2, wintypes.ULONG)
                    release(dialog_pointer)
                ole32.CoUninitialize()

        picker_thread = Thread(target=open_dialog, name="orin-windows-folder-picker", daemon=True)
        picker_thread.start()
        picker_thread.join(DIALOG_TIMEOUT_SECONDS)
        if picker_thread.is_alive():
            return PickResult(path=None, cancelled=False, available=False)
        return result[0] or PickResult(path=None, cancelled=False, available=False)
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def _choose_windows_folder_with_helper(*, timeout: int) -> PickResult | None:
    """Run the COM picker in a GUI Python process when the server has no console."""
    if not sys.platform.startswith("win"):
        return None
    executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.is_file():
        return None
    command = [str(executable), "-m", "agentos.local_workspace.picker", "--native"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )  # noqa: S603 - fixed local picker helper, never user input
    except (OSError, subprocess.SubprocessError):
        return None
    selected = completed.stdout.strip()
    if selected:
        return PickResult(path=selected, cancelled=False, available=True)
    if completed.returncode == 0:
        return PickResult(path=None, cancelled=True, available=True)
    return PickResult(path=None, cancelled=False, available=False)

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
    if command is None and sys.platform.startswith("win"):
        native = _choose_windows_folder_with_helper(timeout=timeout)
        if native is None:
            native = _choose_windows_folder()
        if native is not None:
            return native
        return PickResult(path=None, cancelled=False, available=False)
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


if __name__ == "__main__" and "--native" in sys.argv[1:]:
    native_result = _choose_windows_folder()
    if native_result is not None and native_result.path:
        print(native_result.path, end="")
        raise SystemExit(0)
    if native_result is not None and native_result.cancelled:
        raise SystemExit(0)
    raise SystemExit(1)
