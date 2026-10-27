"""Resolve the person's own cloud key for Live voice STT/TTS.

OpenAI and OpenRouter come from the providers configured in *Configurações →
Provedores* (the same multi-key pool chat turns use). Groq is not a chat provider in Orin, so it
is read from `GROQ_API_KEY` when set.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select

from agentos.live_voice.engines import CloudCredential

_LOGGER = logging.getLogger("agentos.live_voice.credentials")

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
CLOUD_VOICE_PROVIDERS = ("openai", "groq", "openrouter")
_DEFAULT_BASE_URLS = {"openai": OPENAI_DEFAULT_BASE_URL, "openrouter": OPENROUTER_DEFAULT_BASE_URL}


def resolve_cloud_credential(services: Any, user_id: str | None, provider: str) -> CloudCredential | None:
    provider = str(provider or "").strip().lower()
    if provider == "groq":
        key = os.getenv("GROQ_API_KEY", "").strip()
        return CloudCredential("groq", GROQ_BASE_URL, key) if key else None
    if provider not in _DEFAULT_BASE_URLS or not user_id or services is None:
        return None
    try:
        from agentos.api.friend_routes import engine_from_services
        from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
        from agentos.persistence.postgres.schema import provider_configurations
        from agentos.persistence.provider_secrets import ProviderSecretCipher

        engine = engine_from_services(services)
        if engine is None:
            return None
        with engine.connect() as connection:
            row = connection.execute(
                select(provider_configurations.c.base_url, provider_configurations.c.enabled).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
        if row is None or not row["enabled"]:
            return None
        pool = PostgresProviderApiKeyAdapter(engine, cipher=ProviderSecretCipher.from_environment(required=True))
        chosen = pool.next_available_key(user_id, provider)
        if chosen is None or not chosen.plaintext:
            return None
        base_url = str(row["base_url"] or "").strip() or _DEFAULT_BASE_URLS[provider]
        return CloudCredential(provider, base_url, chosen.plaintext)
    except Exception:
        _LOGGER.debug("cloud voice credential lookup failed", exc_info=True)
        return None


__all__ = ["CLOUD_VOICE_PROVIDERS", "GROQ_BASE_URL", "OPENAI_DEFAULT_BASE_URL", "OPENROUTER_DEFAULT_BASE_URL", "resolve_cloud_credential"]
