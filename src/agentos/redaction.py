"""Secret redaction for everything Orin streams or sends away (W0-R11, W7).

Local command hooks and the `orin code` transcript get raw values, as in Claude Code. What leaves through a sink that
other software reads (the activity log and its SSE stream, HTTP hooks, the settings API) goes through these helpers.
They over-redact on purpose: a word like ``author=`` may lose its value, a token never survives.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
MAX_DEPTH = 32

_SECRET_WORD = r"(?:token|secret|passw(?:or)?d|passphrase|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|credential|cookie|session[_-]?id|auth)"
# NAME=value, NAME: value, "NAME": "value", where NAME contains a secret word (GITHUB_TOKEN, db_password, X-Api-Key).
_ASSIGNMENT = re.compile(
    rf"(?i)(?P<name>[\"']?[A-Za-z0-9_.-]*{_SECRET_WORD}[A-Za-z0-9_.-]*[\"']?)(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&|)\]}]+)"
)
# --password x, --token=x, -p is not covered (too ambiguous).
_FLAG = re.compile(rf"(?i)(?P<flag>--?[A-Za-z0-9-]*{_SECRET_WORD}[A-Za-z0-9-]*)(?P<sep>[= ]+)(?P<value>\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s/]+@")
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)", re.IGNORECASE | re.DOTALL)
_KNOWN_TOKENS = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|glpat-[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r")"
)
_SECRET_KEY = re.compile(rf"(?i){_SECRET_WORD}")
# Mappings whose every value is secret whatever its key (environment blocks, HTTP headers).
_SECRET_CONTAINERS = frozenset({"env", "headers", "environment"})


def redact_text(value: str) -> str:
    """``value`` with every secret-looking part replaced by ``[REDACTED]``."""
    if not value:
        return value
    text = _PRIVATE_KEY.sub(REDACTED, value)
    text = _URL_PASSWORD.sub(lambda match: f"{match.group(1)}:{REDACTED}@", text)
    text = _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _ASSIGNMENT.sub(lambda match: f"{match.group('name')}{match.group('sep')}{REDACTED}", text)
    text = _FLAG.sub(lambda match: f"{match.group('flag')}{match.group('sep')}{REDACTED}", text)
    return _KNOWN_TOKENS.sub(REDACTED, text)


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY.search(str(key)))


def redact_value(value: Any, *, _depth: int = 0, _secret: bool = False) -> Any:
    """A copy of a JSON-like value: strings redacted, values under secret keys (and inside ``env``/``headers``)
    replaced entirely. Deeper than ``MAX_DEPTH`` is replaced too (fail closed)."""
    if _depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            hidden = _secret or is_secret_key(name)
            if name.lower() in _SECRET_CONTAINERS and isinstance(item, Mapping):
                result[name] = {str(inner): REDACTED for inner in item}
            elif hidden and not isinstance(item, (Mapping, list, tuple)):
                result[name] = REDACTED if item not in (None, "", True, False) else item
            else:
                result[name] = redact_value(item, _depth=_depth + 1, _secret=hidden)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(item, _depth=_depth + 1, _secret=_secret) for item in value]
    if isinstance(value, str):
        return REDACTED if _secret and value else redact_text(value)
    return value


__all__ = ["REDACTED", "is_secret_key", "redact_text", "redact_value"]
