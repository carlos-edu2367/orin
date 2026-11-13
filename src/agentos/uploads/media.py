"""Name sanitation and content-based classification for user uploads.

The workspace an upload lands in is a real directory the agent can also run
commands in, so the type is decided by the bytes rather than by the extension,
and anything outside the allowlist is refused before it ever reaches disk.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Audio and video are bigger than a photo or a document: their own, higher limit (the bytes are read whole).
MAX_MEDIA_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10
MAX_TURN_BYTES = 100 * 1024 * 1024
MEDIA_KINDS = frozenset({"audio", "video"})
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


def _classify_audio_video(extension: str, data: bytes) -> tuple[str, str] | None:
    """Audio and video containers, recognised by their first bytes (the extension only breaks ties)."""
    head = data[:16]
    if head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", "audio"
    if head[:4] == b"OggS":
        return ("audio/opus" if extension == ".opus" else "audio/ogg"), "audio"
    if head[:4] == b"fLaC":
        return "audio/flac", "audio"
    if head[:3] == b"ID3":
        return "audio/mpeg", "audio"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B ") or extension in {".m4a", ".aac"}:
            return "audio/mp4", "audio"
        if brand == b"qt  " or extension == ".mov":
            return "video/quicktime", "video"
        return "video/mp4", "video"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        # EBML: Matroska or WebM.
        return ("video/x-matroska" if extension == ".mkv" else "video/webm"), "video"
    # Bare frames carry no signature: they count only when the extension says what they are.
    if len(head) >= 2 and head[0] == 0xFF:
        if extension in {".mp3", ".mpga", ".mpeg"} and head[1] & 0xE0 == 0xE0:
            return "audio/mpeg", "audio"
        if extension == ".aac" and head[1] & 0xF6 == 0xF0:
            return "audio/aac", "audio"
    return None


def classify(filename: str, data: bytes) -> tuple[str, str]:
    """Return ``(media_type, kind)``; raise ``UploadRejected`` for anything else.

    ``kind`` is one of ``text``, ``image``, ``pdf``, ``office``, ``audio`` or ``video`` and is what
    the interface and the reading pipeline branch on.
    """
    extension = f".{filename.rpartition('.')[2].lower()}" if "." in filename else ""
    media = _classify_audio_video(extension, data)
    if media is not None:
        return media
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
    "MAX_FILES_PER_MESSAGE", "MAX_MEDIA_UPLOAD_BYTES", "MAX_TURN_BYTES", "MAX_UPLOAD_BYTES", "MEDIA_KINDS",
    "UploadRejected", "classify", "safe_filename",
]
