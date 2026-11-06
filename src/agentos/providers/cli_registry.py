"""Registry for subscription-backed CLI agent adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from agentos.provider_catalog.claude_code import is_claude_cli_bridge_url
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.cli_adapter import CliAgentAdapter

CLI_PROVIDERS: dict[str, Callable[[], CliAgentAdapter]] = {
    "anthropic-cli": ClaudeCliAdapter,
}

_ANTIGRAVITY_RISK_ACKNOWLEDGED_AT: datetime | str | None = None


def _is_valid_risk_acknowledgement(value: object) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, datetime):
        return True
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"false", "0", "no", "none", "null"}:
            return False
        return True
    return False


def set_antigravity_risk_acknowledged(acknowledged_at: datetime | str | bool | None) -> None:
    """Update the in-process cache of Antigravity ToS risk acknowledgement."""
    global _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT
    if not _is_valid_risk_acknowledgement(acknowledged_at):
        _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT = None
    elif isinstance(acknowledged_at, datetime):
        _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT = acknowledged_at
    elif isinstance(acknowledged_at, str):
        _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT = acknowledged_at.strip()
    else:
        _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT = "acknowledged"


def antigravity_risk_acknowledged_at() -> datetime | str | None:
    """Return the active Antigravity risk acknowledgement timestamp/token, if any."""
    return _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT


def cli_adapter_for(
    provider: str,
    base_url: str | None = None,
    *,
    risk_acknowledged_at: object = None,
) -> CliAgentAdapter | None:
    """Resolve the ``CliAgentAdapter`` for a subscription CLI provider, or ``None``."""
    normalized = str(provider or "").strip().lower()
    if normalized == "anthropic":
        if not (base_url and is_claude_cli_bridge_url(base_url)):
            return None
        factory = CLI_PROVIDERS.get("anthropic-cli")
        return factory() if callable(factory) else None
    if normalized == "anthropic-cli":
        factory = CLI_PROVIDERS.get("anthropic-cli")
        return factory() if callable(factory) else None
    if normalized == "openai-codex":
        factory = CLI_PROVIDERS.get("openai-codex")
        return factory() if callable(factory) else None
    if normalized == "google-antigravity":
        effective_ack = (
            _ANTIGRAVITY_RISK_ACKNOWLEDGED_AT
            if risk_acknowledged_at is None
            else risk_acknowledged_at
        )
        if not _is_valid_risk_acknowledgement(effective_ack):
            return None
        factory = CLI_PROVIDERS.get("google-antigravity")
        return factory() if callable(factory) else None
    return None


__all__ = [
    "CLI_PROVIDERS",
    "antigravity_risk_acknowledged_at",
    "cli_adapter_for",
    "set_antigravity_risk_acknowledged",
]
