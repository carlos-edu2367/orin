"""Safe media classification and user-initiated local file opening."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
import subprocess
import sys


# Source files whose extension a machine's MIME table claims for media. On Windows, `mimetypes` reads the
# registry, which maps `.ts`/`.mts` to `video/vnd.dlna.mpeg-tts` (MPEG transport stream): `read_file` then sent
# every TypeScript file to the audio/video transcriber. Orin never transcribes MPEG-TS, so these are code.
_SOURCE_TEXT_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})


def media_type_for(path: Path) -> str:
    if path.suffix.lower() in _SOURCE_TEXT_SUFFIXES:
        return "text/plain"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def open_in_default_application(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]  # no shell, user clicked this action
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, start_new_session=True)  # noqa: S603 - fixed executable, resolved local path
