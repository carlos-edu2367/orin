"""Shared provider transport builder for worker turns and side-channel calls like `/btw`."""
from __future__ import annotations

from collections.abc import Mapping
from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.persistence.postgres.schema import provider_configurations
from agentos.provider_catalog.claude_code import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    is_claude_cli_bridge_url,
    normalize_anthropic_base_url,
)
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL
from agentos.provider_catalog.nvidia import DEFAULT_NVIDIA_BASE_URL, normalize_nvidia_base_url
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, normalize_ollama_base_url
from agentos.provider_catalog.omniroute import DEFAULT_OMNIROUTE_BASE_URL, normalize_omniroute_base_url
from agentos.provider_catalog.opencode import (
    DEFAULT_OPENCODE_LOCAL_BASE_URL,
    OPENCODE_CLOUD_BASE_URL,
    normalize_opencode_base_url,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher

PROVIDER_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "opencode": "https://opencode.ai/zen/v1",
    "openai": "https://api.openai.com/v1",
    "nvidia": DEFAULT_NVIDIA_BASE_URL,
    "omniroute": DEFAULT_OMNIROUTE_BASE_URL,
    "ollama": DEFAULT_OLLAMA_BASE_URL,
}

DEFAULT_MAX_CONTEXT_TOKENS = 60_000
MIN_MAX_CONTEXT_TOKENS = 4_000
CONTEXT_WINDOW_RESERVE_TOKENS = 12_000
OLLAMA_FALLBACK_NUM_CTX = 16_384


def resolve_ollama_num_ctx(context_window: int | None) -> int:
    """Resolve the KV-cache ``num_ctx`` Ollama should allocate for a model window."""
    if context_window is None or context_window <= 0:
        return OLLAMA_FALLBACK_NUM_CTX
    capped = max(
        MIN_MAX_CONTEXT_TOKENS,
        min(DEFAULT_MAX_CONTEXT_TOKENS, context_window - CONTEXT_WINDOW_RESERVE_TOKENS),
    )
    return min(context_window, capped + CONTEXT_WINDOW_RESERVE_TOKENS)


def resolve_provider_base_url(provider: str, credential: Mapping[str, object]) -> str:
    """Resolve the normalized endpoint base URL for a configured provider."""
    configured = str(credential.get("base_url") or "")
    if provider == "anthropic":
        if configured and is_claude_cli_bridge_url(configured):
            return normalize_anthropic_base_url(configured)
        return PROVIDER_BASE_URLS["anthropic"]
    if provider == "omniroute":
        return normalize_omniroute_base_url(configured or DEFAULT_OMNIROUTE_BASE_URL)
    if provider == "ollama":
        return normalize_ollama_base_url(configured or DEFAULT_OLLAMA_BASE_URL)
    if provider == "opencode":
        has_key = bool(str(credential.get("api_key") or "").strip())
        return normalize_opencode_base_url(
            configured or (OPENCODE_CLOUD_BASE_URL if has_key else DEFAULT_OPENCODE_LOCAL_BASE_URL)
        )
    if provider == "nvidia":
        return normalize_nvidia_base_url(configured or DEFAULT_NVIDIA_BASE_URL)
    return PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS["openrouter"])


def build_provider_transport(
    engine: Engine,
    *,
    user_id: str,
    provider: str,
    model_id: str,
    num_ctx: int | None = None,
    cipher_required: bool = True,
    require_existing_key: bool = True,
    key_adapter_cls: type[PostgresProviderApiKeyAdapter] | None = None,
    http_transport_cls: type[HTTPProviderStreamTransport] | None = None,
) -> object:
    """Build a streaming transport (HTTP multi-key or subscription CLI) for `(user_id, provider, model_id)`."""
    resolved_provider = str(provider or "").strip().lower()
    resolved_model_id = str(model_id or "").strip()
    if not resolved_provider or not resolved_model_id:
        raise ValueError("provider and model_id are required")

    with engine.connect() as conn:
        credential = conn.execute(
            select(
                provider_configurations.c.base_url,
                provider_configurations.c.enabled,
                provider_configurations.c.key_cooldown_seconds,
                provider_configurations.c.risk_acknowledged_at,
            ).where(
                provider_configurations.c.user_id == user_id,
                provider_configurations.c.provider == resolved_provider,
            )
        ).mappings().first()

    if credential is None or not credential["enabled"]:
        raise ValueError("provider unavailable")
    if resolved_provider == "google-antigravity" and not credential.get("risk_acknowledged_at"):
        raise ValueError("provider unavailable")

    if resolved_provider in {"openai-codex", "google-antigravity"}:
        from agentos.providers.claude_cli.provider import ClaudeCliProvider
        from agentos.providers.cli_registry import cli_adapter_for

        cli_adapter = cli_adapter_for(
            resolved_provider,
            str(credential.get("base_url") or "") or None,
            risk_acknowledged_at=credential.get("risk_acknowledged_at")
            if resolved_provider == "google-antigravity"
            else None,
        )
        if cli_adapter is None:
            raise ValueError("provider unavailable")
        return ClaudeCliProvider(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model=resolved_model_id,
            adapter=cli_adapter,
        )

    adapter_factory = key_adapter_cls or PostgresProviderApiKeyAdapter
    transport_factory_cls = http_transport_cls or HTTPProviderStreamTransport
    keys = adapter_factory(
        engine,
        cipher=ProviderSecretCipher.from_environment(required=cipher_required),
    )
    first_key = keys.next_available_key(user_id, resolved_provider)
    base_url = resolve_provider_base_url(
        resolved_provider,
        {
            "base_url": credential["base_url"],
            "api_key": first_key.plaintext if first_key is not None else "",
        },
    )

    if resolved_provider == "anthropic" and is_claude_cli_bridge_url(base_url):
        from agentos.providers.claude_cli.provider import ClaudeCliProvider

        return ClaudeCliProvider(base_url=base_url, model=resolved_model_id)

    effective_num_ctx = (
        num_ctx if resolved_provider == "ollama" else None
    )

    def build(api_key: str) -> HTTPProviderStreamTransport:
        return transport_factory_cls(
            provider=resolved_provider,
            base_url=base_url,
            api_key=api_key,
            model=resolved_model_id,
            num_ctx=effective_num_ctx,
        )

    key_is_required = resolved_provider not in PROVIDERS_WITH_BASE_URL or (
        resolved_provider == "anthropic" and not is_claude_cli_bridge_url(base_url)
    )
    if first_key is None:
        if key_is_required:
            if require_existing_key:
                raise ValueError("provider credential is missing")
            return MultiKeyProviderStreamTransport(
                key_pool=keys,
                user_id=user_id,
                provider=resolved_provider,
                cooldown_seconds=int(credential.get("key_cooldown_seconds") or 60),
                transport_factory=build,
                model=resolved_model_id,
                base_url=base_url,
            )
        return build("")

    return MultiKeyProviderStreamTransport(
        key_pool=keys,
        user_id=user_id,
        provider=resolved_provider,
        cooldown_seconds=int(credential.get("key_cooldown_seconds") or 60),
        transport_factory=build,
        model=resolved_model_id,
        base_url=base_url,
    )


__all__ = [
    "CONTEXT_WINDOW_RESERVE_TOKENS",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "MIN_MAX_CONTEXT_TOKENS",
    "OLLAMA_FALLBACK_NUM_CTX",
    "PROVIDER_BASE_URLS",
    "build_provider_transport",
    "resolve_ollama_num_ctx",
    "resolve_provider_base_url",
]
