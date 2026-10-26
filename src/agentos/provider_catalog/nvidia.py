from __future__ import annotations

from urllib.parse import urlparse, urlunparse

import httpx

NVIDIA_CLOUD_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_LOCAL_BASE_URL = "http://localhost:8000/v1"
DEFAULT_NVIDIA_BASE_URL = NVIDIA_CLOUD_BASE_URL


class NvidiaAuthenticationError(RuntimeError):
    pass


def normalize_nvidia_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_NVIDIA_BASE_URL
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("nvidia base_url must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("nvidia base_url must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("nvidia base_url must not include query or fragment")
    hostname = (parsed.hostname or "").lower()
    if (hostname == "nvidia.com" or hostname.endswith(".nvidia.com")) and parsed.scheme != "https":
        raise ValueError("nvidia cloud base_url must use https")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def is_nvidia_cloud(base_url: str) -> bool:
    normalized = normalize_nvidia_base_url(base_url)
    hostname = (urlparse(normalized).hostname or "").lower()
    return hostname == "nvidia.com" or hostname.endswith(".nvidia.com")


class NvidiaModelCatalogClient:
    def __init__(self, *, timeout: float = 10.0, transport: httpx.BaseTransport | None = None) -> None:
        self._timeout = timeout
        self._transport = transport

    def fetch_models(self, api_key: str, *, base_url: str | None = None) -> dict[str, object]:
        normalized_base_url = normalize_nvidia_base_url(base_url or DEFAULT_NVIDIA_BASE_URL)
        headers: dict[str, str] = {}
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            response = client.get(f"{normalized_base_url}/models", headers=headers)
            if response.status_code in (401, 402, 403):
                raise NvidiaAuthenticationError(f"nvidia authentication failed with status {response.status_code}")
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {"data": []}

    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
        payload = self.fetch_models(api_key, base_url=base_url or DEFAULT_NVIDIA_BASE_URL)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ValueError("NVIDIA returned an invalid catalog")
        return data


__all__ = [
    "DEFAULT_NVIDIA_BASE_URL",
    "DEFAULT_NVIDIA_LOCAL_BASE_URL",
    "NVIDIA_CLOUD_BASE_URL",
    "NvidiaAuthenticationError",
    "NvidiaModelCatalogClient",
    "is_nvidia_cloud",
    "normalize_nvidia_base_url",
]
