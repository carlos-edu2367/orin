from __future__ import annotations

from typing import Any

import httpx

from agentos.agentic.provider_stream import HTTPProviderStreamTransport


class OpenRouterModelCatalogClient:
    """Server-side OpenRouter ``GET /api/v1/models`` adapter."""

    def __init__(self, *, base_url: str = "https://openrouter.ai", timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, api_key: str) -> list[dict[str, object]]:
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


# Kept in the provider-catalog edge so production composition can depend on a
# transport port without importing the HTTP implementation into the worker.
ProviderStreamHTTPClient = HTTPProviderStreamTransport


__all__ = ["OpenRouterModelCatalogClient", "ProviderStreamHTTPClient"]
