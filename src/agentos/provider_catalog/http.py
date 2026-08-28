from __future__ import annotations

from typing import Any

import httpx

from agentos.agentic.provider_stream import HTTPProviderStreamTransport


class OpenRouterModelCatalogClient:
    """Server-side OpenRouter ``GET /api/v1/models`` adapter."""

    def __init__(self, *, base_url: str = "https://openrouter.ai", timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
        response = httpx.get(
            f"{self._base_url}/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload: Any = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ValueError("OpenRouter returned an invalid catalog")
        return data


class OpenAIModelCatalogClient:
    """Server-side OpenAI ``GET /v1/models`` adapter."""

    def __init__(self, *, base_url: str = "https://api.openai.com/v1", timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
        response = httpx.get(
            f"{self._base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload: Any = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ValueError("OpenAI returned an invalid catalog")
        return data


class AnthropicModelCatalogClient:
    """Server-side Anthropic ``GET /v1/models`` adapter with cursor paging."""

    def __init__(self, *, base_url: str = "https://api.anthropic.com", timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
        models: list[dict[str, object]] = []
        after_id: str | None = None
        # The API's page size is capped at 1000.  The defensive cap prevents a
        # malformed upstream cursor from creating an unbounded refresh.
        for _ in range(100):
            params: dict[str, object] = {"limit": 1000}
            if after_id is not None:
                params["after_id"] = after_id
            response = httpx.get(
                f"{self._base_url}/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                params=params,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload: Any = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise ValueError("Anthropic returned an invalid catalog")
            models.extend(data)
            if payload.get("has_more") is not True:
                return models
            next_cursor = payload.get("last_id")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == after_id:
                raise ValueError("Anthropic returned an invalid catalog cursor")
            after_id = next_cursor
        raise ValueError("Anthropic catalog pagination exceeded its safety limit")


# Kept in the provider-catalog edge so production composition can depend on a
# transport port without importing the HTTP implementation into the worker.
ProviderStreamHTTPClient = HTTPProviderStreamTransport


__all__ = [
    "AnthropicModelCatalogClient", "OpenAIModelCatalogClient",
    "OpenRouterModelCatalogClient", "ProviderStreamHTTPClient",
]
