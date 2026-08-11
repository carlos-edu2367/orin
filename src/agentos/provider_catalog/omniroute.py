"""Thin client for OmniRoute's documented OpenAI-compatible public API.

Only ``/v1/models`` is used here.  Management endpoints intentionally remain
outside AgentOS: routing, provider connections and fallback policy are owned by
the separately-running OmniRoute instance.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


DEFAULT_OMNIROUTE_BASE_URL = "http://localhost:20128/v1"


def normalize_omniroute_base_url(value: str) -> str:
    """Validate the public gateway URL without retaining credentials."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OmniRoute base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OmniRoute base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OmniRoute base URL must not contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    if not path.endswith("/v1"):
        raise ValueError("OmniRoute base URL must end in /v1")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class OmniRouteCatalogClient:
    """Fetch the credential-scoped model/route catalog from OmniRoute."""

    def __init__(self, *, timeout: float = 15.0, client: httpx.Client | None = None) -> None:
        self._timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"OmniRouteCatalogClient(timeout={self._timeout!r})"

    def fetch(self, api_key: str, *, base_url: str = DEFAULT_OMNIROUTE_BASE_URL) -> list[dict[str, object]]:
        endpoint = f"{normalize_omniroute_base_url(base_url)}/models"
        try:
            if self._client is None:
                response = httpx.get(endpoint, headers=_headers(api_key), timeout=self._timeout)
            else:
                response = self._client.get(endpoint, headers=_headers(api_key), timeout=self._timeout)
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RuntimeError("OmniRoute connection failed") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("OmniRoute connection failed")
        return [_safe_model(item) for item in payload["data"] if isinstance(item, dict)]


def _headers(api_key: str) -> dict[str, str]:
    headers = {"accept": "application/json"}
    if api_key.strip():
        headers["authorization"] = f"Bearer {api_key}"
    return headers


def _safe_model(item: dict[str, Any]) -> dict[str, object]:
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise RuntimeError("OmniRoute connection failed")
    capabilities = item.get("supported_parameters")
    inputs = item.get("input_modalities")
    return {
        "id": model_id.strip(),
        "name": _text(item.get("name")) or model_id.strip(),
        "context_length": _positive(item.get("context_length")),
        "supported_parameters": _strings(capabilities),
        "input_modalities": _strings(inputs),
        # The OpenAI-compatible /v1/models response has no stable combo flag.
        # Auto routes are documented public IDs; other routes remain opaque IDs.
        "route_kind": "auto" if model_id.strip() == "auto" or model_id.strip().startswith("auto/") else "model",
    }


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _strings(value: object) -> list[str]:
    return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


__all__ = ["DEFAULT_OMNIROUTE_BASE_URL", "OmniRouteCatalogClient", "normalize_omniroute_base_url"]
