from __future__ import annotations

import hashlib
import re

from .models import ArtifactNamespace


_SECRET = re.compile(r"(?i)(password|passwd|secret|token|credential|api[_-]?key|private[_-]?key)")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")


def derive_namespace(user_id: str, workspace_id: str | None, category: str) -> ArtifactNamespace:
    raw = f"{user_id}\0{workspace_id or ''}\0{category}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return ArtifactNamespace(f"artifact-ns-{digest}")


def sanitize_logical_name(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValueError("logical_name is invalid")
    if ".." in value or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("logical_name is invalid")
    if _SECRET.search(value) or _SAFE_NAME.fullmatch(value) is None:
        raise ValueError("logical_name is invalid")
    return value


def sanitize_public_reason(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("reason is invalid")
    sanitized = _SECRET.sub("redacted", value)
    sanitized = re.sub(r"(?:[A-Za-z]:)?[/\\][^ ]+", "redacted", sanitized)
    sanitized = re.sub(r"https?://\S+", "redacted", sanitized)
    return sanitized[:128] or "unspecified"


def contains_secret(value: object) -> bool:
    return bool(_SECRET.search(str(value)))


__all__ = ["contains_secret", "derive_namespace", "sanitize_logical_name", "sanitize_public_reason"]
