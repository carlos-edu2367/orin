"""Name sanitation and content-based classification for user uploads.

The workspace an upload lands in is a real directory the agent can also run
commands in, so the type is decided by the bytes rather than by the extension,
and anything outside the allowlist is refused before it ever reaches disk.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10
MAX_TURN_BYTES = 50 * 1024 * 1024
MAX_FILENAME_CHARS = 120

_UNSAFE = re.compile(r"[^A-Za-z0-9._ ()\-]")
_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])$")

_TEXT_EXTENSIONS = {
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
    ".py": "text/x-python", ".js": "text/javascript", ".ts": "text/typescript",
    ".tsx": "text/typescript", ".jsx": "text/javascript", ".html": "text/html",
    ".css": "text/css", ".sql": "text/plain", ".sh": "text/x-shellscript",
    ".ini": "text/plain", ".toml": "text/plain", ".log": "text/plain",
    ".xml": "text/xml",
}
_OFFICE_EXTENSIONS = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class UploadRejected(ValueError):
    """The upload was refused before anything was written to disk."""


def safe_filename(name: str) -> str:
    """Reduce a client-supplied name to a bounded, inert file name."""
    base = PurePosixPath(str(name or "").replace("\\", "/")).name
    cleaned = _UNSAFE.sub("", base.replace("\x00", " ")).strip(" .")
    if not cleaned:
        return "arquivo"
    stem, dot, extension = cleaned.rpartition(".")
    if not dot:
        stem, extension = cleaned, ""
    if _RESERVED.fullmatch(stem):
        stem = "arquivo"
    if not stem:
        stem = "arquivo"
    suffix = f".{extension}" if extension else ""
    return f"{stem[: MAX_FILENAME_CHARS - len(suffix)]}{suffix}"


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def classify(filename: str, data: bytes) -> tuple[str, str]:
    """Return ``(media_type, kind)``; raise ``UploadRejected`` for anything else.

    ``kind`` is one of ``text``, ``image``, ``pdf`` or ``office`` and is what
    the interface and the reading pipeline branch on.
    """
    extension = f".{filename.rpartition('.')[2].lower()}" if "." in filename else ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "image"
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if data.startswith(b"PK\x03\x04"):
        if extension in _OFFICE_EXTENSIONS:
            return _OFFICE_EXTENSIONS[extension], "office"
        raise UploadRejected("compressed files are not accepted")
    if extension in _TEXT_EXTENSIONS and _is_text(data):
        return _TEXT_EXTENSIONS[extension], "text"
    if not extension and _is_text(data):
        return "text/plain", "text"
    raise UploadRejected("file type is not accepted")


__all__ = [
    "MAX_FILES_PER_MESSAGE", "MAX_TURN_BYTES", "MAX_UPLOAD_BYTES",
    "UploadRejected", "classify", "safe_filename",
]
