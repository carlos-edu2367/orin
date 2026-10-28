"""Resolve the person's own cloud key for Live voice STT/TTS.

OpenAI and OpenRouter come from the providers configured in *Configurações →
Provedores* (the same multi-key pool chat turns use). Groq is not a chat provider in Orin, so it
is read from `GROQ_API_KEY` when set.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from sqlalchemy import select

from agentos.live_voice.engines import CloudCredential

_LOGGER = logging.getLogger("agentos.live_voice.credentials")

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
CLOUD_VOICE_PROVIDERS = ("openai", "groq", "openrouter")
_DEFAULT_BASE_URLS = {"openai": OPENAI_DEFAULT_BASE_URL, "openrouter": OPENROUTER_DEFAULT_BASE_URL}

CREDENTIAL_CACHE_TTL_SECONDS = 15.0
_CREDENTIAL_CACHE: dict[tuple[int, int, str, str], tuple[float, CloudCredential | None]] = {}
_CREDENTIAL_CACHE_LOCK = threading.Lock()


def clear_credential_cache() -> None:
    with _CREDENTIAL_CACHE_LOCK:
        _CREDENTIAL_CACHE.clear()


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

        cache_key = (id(services), id(engine), str(user_id), provider)
        now = time.monotonic()
        with _CREDENTIAL_CACHE_LOCK:
            cached = _CREDENTIAL_CACHE.get(cache_key)
            if cached is not None and (now - cached[0]) < CREDENTIAL_CACHE_TTL_SECONDS:
                return cached[1]

        with engine.connect() as connection:
            row = connection.execute(
                select(provider_configurations.c.base_url, provider_configurations.c.enabled).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
        if row is None or not row["enabled"]:
            with _CREDENTIAL_CACHE_LOCK:
                _CREDENTIAL_CACHE[cache_key] = (now, None)
            return None
        pool = PostgresProviderApiKeyAdapter(engine, cipher=ProviderSecretCipher.from_environment(required=True))
        chosen = pool.next_available_key(user_id, provider)
        if chosen is None or not chosen.plaintext:
            return None
        base_url = str(row["base_url"] or "").strip() or _DEFAULT_BASE_URLS[provider]
        cred = CloudCredential(provider, base_url, chosen.plaintext)
        with _CREDENTIAL_CACHE_LOCK:
            _CREDENTIAL_CACHE[cache_key] = (now, cred)
        return cred
    except Exception:
        _LOGGER.debug("cloud voice credential lookup failed", exc_info=True)
        return None


__all__ = [
    "CLOUD_VOICE_PROVIDERS",
    "CREDENTIAL_CACHE_TTL_SECONDS",
    "GROQ_BASE_URL",
    "OPENAI_DEFAULT_BASE_URL",
    "OPENROUTER_DEFAULT_BASE_URL",
    "clear_credential_cache",
    "resolve_cloud_credential",
]
