from __future__ import annotations

import re


_SENSITIVE_PARTS = (
    "api_key",
    "access_token",
    "refresh_token",
    "credential",
    "cookie",
    "password",
    "secret",
    "prompt_text",
    "full_prompt",
    "full_text",
    "raw_input",
    "raw_output",
    "stack_trace",
)


class AgentSecurityError(ValueError):
    """Sanitized public validation failure for Agent contracts."""


def require_text(value: object, field: str, *, maximum: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AgentSecurityError(f"{field} is invalid")
    if maximum is not None and len(value) > maximum:
        raise AgentSecurityError(f"{field} exceeds its limit")


def require_aware(value: object, field: str) -> None:
    if not hasattr(value, "tzinfo") or value.tzinfo is None or value.utcoffset() is None:
        raise AgentSecurityError(f"{field} must be timezone-aware")


def contains_secret(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def validate_reference(value: str, field: str = "reference") -> None:
    require_text(value, field, maximum=256)
    if contains_secret(value):
        raise AgentSecurityError(f"{field} is not a permitted opaque reference")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value):
        raise AgentSecurityError(f"{field} has an invalid reference format")


def sanitize_public_error(message: str) -> None:
    """Convert arbitrary diagnostics into a category-only public error."""

    if not isinstance(message, str):
        raise AgentSecurityError("agent operation rejected")
    normalized = message.lower()
    if contains_secret(normalized) or "=" in normalized or "{" in normalized:
        raise AgentSecurityError("agent operation rejected for protected data")
    raise AgentSecurityError("agent operation rejected")


def require_same_scope(
    *,
    expected_user_id: str,
    expected_workspace_id: str | None,
    actual_user_id: str,
    actual_workspace_id: str | None,
) -> None:
    if expected_user_id != actual_user_id or expected_workspace_id != actual_workspace_id:
        raise AgentSecurityError("agent access denied")


def classification_allows(clearance: str, classification: str) -> bool:
    order = {"INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}
    try:
        return order[str(clearance)] >= order[str(classification)]
    except KeyError as exc:
        raise AgentSecurityError("classification is invalid") from exc
