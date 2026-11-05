"""Line-ending helpers so edits and rewrites keep a file's own convention."""
from __future__ import annotations


def dominant_newline(text: str) -> str:
    """``"\r\n"`` when CRLF outnumbers bare LF in ``text``, otherwise ``"\n"``."""
    crlf = text.count("\r\n")
    bare_lf = text.count("\n") - crlf
    return "\r\n" if crlf > bare_lf else "\n"


def with_newline(text: str, newline: str) -> str:
    """Normalise every line break in ``text`` to ``\n``, then to CRLF when asked."""
    normalized = text.replace("\r\n", "\n")
    return normalized.replace("\n", "\r\n") if newline == "\r\n" else normalized
