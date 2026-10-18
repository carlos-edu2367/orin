"""Variables a SessionStart hook wrote to ORIN_ENV_FILE / CLAUDE_ENV_FILE."""

from __future__ import annotations

import re
from pathlib import Path

MAX_FILE_BYTES = 256 * 1024
MAX_VARS = 100
MAX_VALUE = 8192
_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env_file(path: Path) -> dict[str, str]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return {}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value[:MAX_VALUE]
        if len(values) >= MAX_VARS:
            break
    return values
