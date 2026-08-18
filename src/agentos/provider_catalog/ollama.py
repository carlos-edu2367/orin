"""Ollama provider edge: base URL rules and the model catalog client.

One provider row serves both a local instance and Ollama Cloud.  The mode is
derived from the configured host rather than stored as a separate flag, so
there is exactly one place that can disagree about which one is in use.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CLOUD_BASE_URL = "https://ollama.com"
_CLOUD_HOST = "ollama.com"


class OllamaCloudAuthenticationError(RuntimeError):
    """The hosted Ollama API rejected the supplied credential."""


def normalize_ollama_base_url(value: str) -> str:
    """Validate the origin Ollama is served from, without retaining credentials."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Ollama base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Ollama base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ollama base URL must not contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    # The native API lives at /api/*, so the stored value is the bare origin.
    # A pasted /v1 (the OpenAI-compatible prefix) or /api is dropped rather
    # than rejected: both are what a user copies out of the Ollama docs.
    for suffix in ("/v1", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def is_ollama_cloud(base_url: str) -> bool:
    """Whether a normalized base URL points at the hosted service."""
    host = (urlsplit(base_url).hostname or "").lower()
    return host == _CLOUD_HOST or host.endswith(f".{_CLOUD_HOST}")


class OllamaCatalogClient:
    """Fetch the installed/available model catalog from an Ollama instance.

    ``/api/tags`` lists models but reports neither the context window nor the
    capabilities, and both matter here: the window becomes the turn's
    ``num_ctx``, and a model without ``tools`` cannot drive the agentic loop
    at all.  Only ``/api/show`` knows them, so each listed model costs one
    extra call.
    """

    def __init__(self, *, timeout: float = 15.0, client: httpx.Client | None = None) -> None:
        self._timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"OllamaCatalogClient(timeout={self._timeout!r})"

    def fetch(self, api_key: str, *, base_url: str = DEFAULT_OLLAMA_BASE_URL) -> list[dict[str, object]]:
        base = normalize_ollama_base_url(base_url or DEFAULT_OLLAMA_BASE_URL)
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            payload = self._json(client, "GET", f"{base}/api/tags", api_key, None)
            if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                raise RuntimeError("Ollama connection failed")
            return [self._model(client, base, api_key, item) for item in payload["models"] if isinstance(item, dict)]
        finally:
            if self._client is None:
                client.close()

    def verify_cloud_access(self, api_key: str, *, base_url: str, model: str) -> None:
        """Verify a Cloud key against an inference endpoint.

        ``/api/tags`` and ``/api/show`` are useful for catalog discovery but do
        not prove that a key can generate a response. Cloud access is only
        authenticated when the request reaches ``/api/chat``.
        """
        if not api_key.strip():
            raise OllamaCloudAuthenticationError("Ollama Cloud API key is required")
        base = normalize_ollama_base_url(base_url)
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            self._json(
                client,
                "POST",
                f"{base}/api/chat",
                api_key,
                {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "stream": False, "options": {"num_predict": 1}},
            )
        finally:
            if self._client is None:
                client.close()

    def _model(self, client: httpx.Client, base: str, api_key: str, item: dict[str, Any]) -> dict[str, object]:
        name = _text(item.get("model")) or _text(item.get("name"))
        if name is None:
            raise RuntimeError("Ollama connection failed")
        try:
            shown = self._json(client, "POST", f"{base}/api/show", api_key, {"model": name})
        except RuntimeError:
            # A model whose details cannot be read still belongs in the
            # catalog; dropping the whole refresh over one of them would be a
            # worse trade than listing it without a window or capabilities.
            shown = {}
        detail = shown if isinstance(shown, dict) else {}
        return {
            "id": name,
            "name": _text(item.get("name")) or name,
            "context_length": _context_length(detail.get("model_info")),
            "capabilities": _strings(detail.get("capabilities")),
        }

    def _json(self, client: httpx.Client, method: str, url: str, api_key: str, body: dict[str, object] | None) -> Any:
        headers = {"accept": "application/json"}
        if api_key.strip():
            headers["authorization"] = f"Bearer {api_key}"
        try:
            response = client.request(method, url, headers=headers, json=body, timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise OllamaCloudAuthenticationError("Ollama Cloud credentials rejected") from error
            raise RuntimeError("Ollama connection failed") from error
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RuntimeError("Ollama connection failed") from error


def _context_length(value: object) -> int | None:
    """Read ``<architecture>.context_length`` without knowing the architecture."""
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if isinstance(key, str) and key.endswith(".context_length"):
            if isinstance(item, int) and not isinstance(item, bool) and item > 0:
                return item
    return None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(value: object) -> list[str]:
    return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


__all__ = [
    "DEFAULT_OLLAMA_BASE_URL",
    "OLLAMA_CLOUD_BASE_URL",
    "OllamaCatalogClient",
    "OllamaCloudAuthenticationError",
    "is_ollama_cloud",
    "normalize_ollama_base_url",
]
