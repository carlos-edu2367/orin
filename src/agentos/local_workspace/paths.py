"""Normalisation, risk labelling and inspection of a user-chosen folder.

No folder is refused on policy grounds: the machine belongs to the person, and
pointing an agent at a whole disk is a legitimate request. ``classify_risk``
exists so the interface can name the consequence of a broad choice, never to
block it. Only facts block: a path that does not exist, is not a directory, or
cannot be written to.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

MAX_PATH_CHARS = 4096
MAX_COUNTED_ENTRIES = 500


class FolderRejected(ValueError):
    """The text could not be read as an absolute local path."""


@dataclass(frozen=True, slots=True)
class FolderInspection:
    path: str
    exists: bool
    is_directory: bool
    writable: bool
    entry_count: int
    entries_truncated: bool
    risk: str


def normalize_path(value: str) -> Path:
    if not isinstance(value, str):
        raise FolderRejected("path must be text")
    text = value.strip().strip('"')
    if not text or len(text) > MAX_PATH_CHARS:
        raise FolderRejected("path must be a bounded non-blank value")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise FolderRejected("path must be absolute")
    try:
        return candidate.resolve()
    except OSError as error:
        raise FolderRejected("path could not be resolved") from error


def system_prefixes() -> tuple[Path, ...]:
    if sys.platform.startswith("win"):
        names = (os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        return tuple(Path(name) for name in names if name)
    return tuple(Path(name) for name in ("/etc", "/usr", "/bin", "/sbin", "/var", "/System", "/Library"))


def classify_risk(path: Path, *, home: Path, orin_data: Path, system_prefixes: tuple[Path, ...]) -> str:
    if path == Path(path.anchor):
        return "drive_root"
    for prefix in system_prefixes:
        if path == prefix or _inside(path, prefix):
            return "system"
    if path == home:
        return "home_root"
    if path == orin_data or _inside(path, orin_data):
        return "orin_data"
    return "none"


def inspect_folder(value: str, *, home: Path, orin_data: Path, prefixes: tuple[Path, ...] | None = None) -> FolderInspection:
    path = normalize_path(value)
    risk = classify_risk(path, home=home, orin_data=orin_data, system_prefixes=prefixes if prefixes is not None else system_prefixes())
    exists = path.exists()
    is_directory = path.is_dir()
    writable = is_directory and os.access(path, os.W_OK)
    count, truncated = _count_entries(path) if is_directory else (0, False)
    return FolderInspection(str(path), exists, is_directory, writable, count, truncated, risk)


def _inside(path: Path, prefix: Path) -> bool:
    try:
        return path.is_relative_to(prefix)
    except (OSError, ValueError):
        return False


def _count_entries(path: Path) -> tuple[int, bool]:
    """Count the first level only, bounded, so a huge folder cannot stall the request."""
    count = 0
    try:
        with os.scandir(path) as entries:
            for _ in entries:
                count += 1
                if count >= MAX_COUNTED_ENTRIES:
                    return count, True
    except OSError:
        return 0, False
    return count, False
