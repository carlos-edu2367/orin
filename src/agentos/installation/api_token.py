"""The per-run API token ``orin code`` authenticates with (spec §9.1).

The launcher issues a fresh random token every time it starts the runtime, in ``ORIN_RUN_DIR/api-token``, readable
only by the person who started it, and removes it on shutdown. The local API accepts it as a bearer credential next to
the loopback check; the browser keeps using loopback alone. The token is what separates a call made by ``orin code``
(which may carry CLI-layer settings) from any other local request.
"""

from __future__ import annotations

import csv
import logging
import os
import secrets
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILE_NAME = "api-token"
_TOKEN_BYTES = 32
_MAX_TOKEN_CHARS = 256


class ApiTokenError(RuntimeError):
    """The token could not be written with owner-only access, so none was issued."""


def token_path(run_dir: Path) -> Path:
    return Path(run_dir) / TOKEN_FILE_NAME


def _system_tool(name: str) -> str:
    # Never resolved through PATH or the current folder: a planted icacls.exe must not run instead of Windows' own.
    return str(Path(os.environ.get("SystemRoot") or "C:/Windows") / "System32" / name)


def _windows_user_sid() -> str:
    """The current account's SID, which icacls takes as ``*SID`` whatever characters the account name has."""
    result = subprocess.run(
        [_system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    rows = list(csv.reader((result.stdout or "").splitlines()))
    sid = rows[0][-1].strip() if result.returncode == 0 and rows and rows[0] else ""
    if not sid.startswith("S-1-"):
        raise ApiTokenError("the current Windows account could not be identified")
    return sid


def _windows_owner_only(path: Path) -> None:
    """Replace the whole access list with one entry for the current account, protected from inheritance.

    Dropping inherited entries is not enough: an administrator account creates files with explicit SYSTEM,
    Administrators and OWNER RIGHTS entries (seen on CI), so the list is set whole rather than edited.
    """
    import ctypes
    from ctypes import wintypes

    sid = _windows_user_sid()
    if not all(part.isdigit() for part in sid[2:].split("-")):
        raise ApiTokenError("the current Windows account could not be identified")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    # D:P = a protected DACL (nothing inherited); FA = full access; one entry, for this account's SID only.
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"D:P(A;;FA;;;{sid})", 1, ctypes.byref(descriptor), None):
        raise ApiTokenError(f"the owner-only access list could not be built (error {ctypes.get_last_error()})")
    try:
        dacl_security_information, protected_dacl_security_information = 0x00000004, 0x80000000
        if not advapi32.SetFileSecurityW(str(path), dacl_security_information | protected_dacl_security_information, descriptor):
            raise ApiTokenError(f"{path.name} could not be made owner-only (error {ctypes.get_last_error()})")
    finally:
        kernel32.LocalFree(descriptor)


def restrict_to_owner(path: Path) -> None:
    """Owner-only access, or ``ApiTokenError``."""
    if os.name == "nt":
        _windows_owner_only(path)
    else:
        os.chmod(path, 0o600)


def issue_api_token(run_dir: Path) -> str:
    """Write a new token (replacing any earlier one) and return it; nothing is left behind on failure."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    temporary = run_dir / f".{TOKEN_FILE_NAME}.{secrets.token_hex(4)}.tmp"
    try:
        # Created owner-only on POSIX from the first byte; Windows is restricted before the token is written.
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        restrict_to_owner(temporary)
        temporary.write_text(token, encoding="ascii")
        os.replace(temporary, token_path(run_dir))
    except Exception as error:
        try:
            temporary.unlink()
        except OSError:
            pass
        if isinstance(error, ApiTokenError):
            raise
        raise ApiTokenError(f"the API token could not be written: {error}") from error
    return token


def read_api_token(run_dir: Path) -> str | None:
    """The current run's token, or None when no runtime issued one."""
    try:
        text = token_path(run_dir).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if 0 < len(text) <= _MAX_TOKEN_CHARS else None


def revoke_api_token(run_dir: Path) -> None:
    try:
        token_path(run_dir).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("the API token could not be removed", exc_info=True)


__all__ = ["ApiTokenError", "TOKEN_FILE_NAME", "issue_api_token", "read_api_token", "restrict_to_owner", "revoke_api_token", "token_path"]
