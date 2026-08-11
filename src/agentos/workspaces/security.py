from __future__ import annotations

import re

from .models import MAX_REASON, MAX_TEXT


_PHYSICAL_MARKERS = ("/", "\\", ":", "//", "..")


def reject_physical_root_input(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(marker in value for marker in _PHYSICAL_MARKERS):
        raise ValueError("physical root input is not accepted")
    if value.lower().startswith(("http:", "https:", "file:", "\\\\?\\", "\\\\.\\")):
        raise ValueError("physical root input is not accepted")


def validate_logical_path(value: str) -> str:
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError("logical path must be one non-empty segment")
    if any(marker in value for marker in _PHYSICAL_MARKERS) or any(ord(char) < 32 for char in value):
        raise ValueError("logical path must be relative and safe")
    return value


def sanitize_display_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > MAX_TEXT or any(ord(char) < 32 for char in value):
        raise ValueError("display name is invalid")
    return value


def sanitize_public_reason(value: str) -> str:
    if not isinstance(value, str):
        return "unspecified"
    value = re.sub(r"(?i)(path|root|handle|token|secret|password|credential|dsn)\s*[:=]\s*[^\s,;]+", lambda match: f"{match.group(1)}=<redacted>", value)
    value = value.replace("\\", "<redacted>")
    return value.strip()[:MAX_REASON] or "unspecified"


def validate_actor_binding(user_id: str, actor: str, agent_id: str) -> None:
    if actor == f"user:{user_id}" or actor == f"agent:{agent_id}" or actor == f"system:{user_id}":
        return
    raise PermissionError("actor is not bound to workspace ownership")


__all__ = ["reject_physical_root_input", "sanitize_display_name", "sanitize_public_reason", "validate_actor_binding", "validate_logical_path"]
