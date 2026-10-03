"""Safe .zip import into a profile's file area.

Header sizes are only used for a quick refusal; the real limit is enforced on
the bytes actually written. Everything lands in a hidden temporary folder and
is renamed into place only when the whole archive was accepted.
"""
from __future__ import annotations

import shutil
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from .binding import ProfileFolderRejected, relative_display, resolve_inside

_CHUNK = 1024 * 1024


class ArchiveRejected(ValueError):
    pass


def _safe_parts(name: str) -> list[str]:
    raw = name.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ArchiveRejected(f"absolute path in archive: {name}")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ArchiveRejected(f"path escapes the archive: {name}")
    return parts


def extract_zip(source: BinaryIO, root: Path, parent: str, folder_name: str, *, max_total_bytes: int = 2 * 1024**3, max_entries: int = 50_000, max_ratio: int = 200) -> str:
    clean = (folder_name or "").strip()
    if clean in ("", ".", "..") or "/" in clean or "\\" in clean or len(clean) > 255:
        raise ArchiveRejected("folder name is not valid")
    try:
        base = resolve_inside(root, parent)
    except ProfileFolderRejected as error:
        raise ArchiveRejected(str(error)) from error
    destination = base / clean
    if destination.exists():
        raise ArchiveRejected("destination already exists")
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise ArchiveRejected("not a zip archive") from error
    staging = base / f".import-{uuid4().hex}"
    try:
        with archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise ArchiveRejected("archive has too many entries")
            written = 0
            staging.mkdir()
            for member in members:
                parts = _safe_parts(member.filename)
                if not parts:
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    continue
                target = staging.joinpath(*parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.file_size > _CHUNK and member.file_size > max_ratio * max(member.compress_size, 1):
                    raise ArchiveRejected(f"suspicious compression ratio: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as reader, open(target, "wb") as writer:
                    while chunk := reader.read(_CHUNK):
                        written += len(chunk)
                        if written > max_total_bytes:
                            raise ArchiveRejected("archive is larger than allowed")
                        writer.write(chunk)
        staging.rename(destination)
    except ArchiveRejected:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, NotImplementedError, ValueError) as error:
        # A corrupted member, an encrypted entry, an unsupported compression
        # method, or a file and a folder sharing one name: all of them are a
        # bad upload, never a server error, and nothing half-written remains.
        shutil.rmtree(staging, ignore_errors=True)
        raise ArchiveRejected("archive could not be extracted") from error
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return relative_display(root, destination) or clean


__all__ = ["ArchiveRejected", "extract_zip"]
