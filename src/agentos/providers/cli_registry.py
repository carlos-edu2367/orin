"""Registry for subscription-backed CLI agent adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from agentos.provider_catalog.claude_code import is_claude_cli_bridge_url
from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.cli_adapter import CliAgentAdapter
from agentos.providers.codex_cli.adapter import CodexCliAdapter

CLI_PROVIDERS: dict[str, Callable[[], CliAgentAdapter]] = {
    "anthropic-cli": ClaudeCliAdapter,
    "openai-codex": CodexCliAdapter,
    "google-antigravity": AntigravityCliAdapter,
}


def _is_valid_risk_acknowledgement(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return value is True
    if isinstance(value, datetime):
        return True
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"false", "0", "no", "none", "null"}:
            return False
        normalized = (
            f"{cleaned[:-1]}+00:00"
            if cleaned.endswith(("Z", "z"))
            else cleaned
        )
        try:
            datetime.fromisoformat(normalized)
            return True
        except ValueError:
            return False
    return False


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
        if not _is_valid_risk_acknowledgement(risk_acknowledged_at):
            return None
        factory = CLI_PROVIDERS.get("google-antigravity")
        return factory() if callable(factory) else None
    return None


__all__ = [
    "CLI_PROVIDERS",
    "cli_adapter_for",
]
