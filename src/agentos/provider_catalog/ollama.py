"""Ollama provider edge: base URL rules and the model catalog client.

One provider row serves both a local instance and Ollama Cloud.  The mode is
derived from the configured host rather than stored as a separate flag, so
there is exactly one place that can disagree about which one is in use.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CLOUD_BASE_URL = "https://ollama.com"
_CLOUD_HOST = "ollama.com"


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


__all__ = [
    "DEFAULT_OLLAMA_BASE_URL",
    "OLLAMA_CLOUD_BASE_URL",
    "is_ollama_cloud",
    "normalize_ollama_base_url",
]
