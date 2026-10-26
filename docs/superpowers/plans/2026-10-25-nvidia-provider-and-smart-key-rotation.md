# NVIDIA Provider & Smart Multi-Key Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class support for the NVIDIA (`nvidia`) provider (both NVIDIA NIM Cloud `https://integrate.api.nvidia.com/v1` with free/paid `nvapi-...` keys and customizable Self-Hosted NIM endpoints) and upgrade the multi-key pool across the entire ecosystem (`provider_stream`, `chat.py`, subagents/vision, and catalog refresh) to differentiate temporary rate limits (`cooldown`) from exhausted/out-of-usage keys (`exhausted`) with manual UI reactivation.

**Architecture:**
- Create `src/agentos/provider_catalog/nvidia.py` for URL normalization (`normalize_nvidia_base_url`, `is_nvidia_cloud`), SSRF-safe validation, and `/v1/models` fetching via `NvidiaModelCatalogClient`.
- Extend `PostgresProviderApiKeyAdapter` with `mark_exhausted(key_id)` and `activate_key(command)`, upgrading `next_available_key` to a 3-tier priority selector: Tier 1 (`active` or expired `cooldown`) -> Tier 2 (unexpired `cooldown` fallback) -> Tier 3 (`exhausted` last-resort fallback).
- Enhance `ProviderKeyRejected` (`exhausted: bool`, `retry_after_seconds: int | None`) and `HTTPProviderStreamTransport` to detect HTTP `402`, `401/403`, `429` (differentiating RPM rate-limits from quota/credit exhaustion), `400/422` quota messages, and 1st-SSE-event quota/auth errors before any content token is emitted.
- Extend `MultiKeyProviderStreamTransport` and `ProviderModelCatalogService.refresh` so main chat turns, subagents, vision reading (`ChatWorker._transport_for`), and catalog refreshes all auto-rotate keys and record `exhausted` vs `cooldown` status.
- Add `POST /v1/providers/nvidia/test` and `POST /v1/providers/{provider}/keys/{key_id}:activate` in `gateway.py`, plus `NvidiaSetup.tsx`, NVIDIA branding, and exhausted-key badge + "Reativar" button in `ProviderKeyList.tsx`.

**Tech Stack:** Python 3.12+, SQLAlchemy Core, `httpx`, `pytest`, React 18, TypeScript, Tailwind CSS, Vitest, Playwright.

---

### Task 1: NVIDIA Catalog Client, URL Normalization & Pricing Fallback

**Files:**
- Create: `src/agentos/provider_catalog/nvidia.py`
- Modify: `src/agentos/provider_catalog/models.py`
- Modify: `src/agentos/provider_catalog/service.py`
- Modify: `src/agentos/provider_catalog/resolver_catalog.py`
- Modify: `src/agentos/provider_catalog/pricing.py`
- Test: `tests/unit/provider_catalog/test_nvidia.py`

- [ ] **Step 1: Write the failing unit tests in `tests/unit/provider_catalog/test_nvidia.py`**

```python
from __future__ import annotations

import httpx
import pytest

from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL, PROVIDERS_WITH_OPTIONAL_KEY
from agentos.provider_catalog.nvidia import (
    DEFAULT_NVIDIA_BASE_URL,
    DEFAULT_NVIDIA_LOCAL_BASE_URL,
    NVIDIA_CLOUD_BASE_URL,
    NvidiaAuthenticationError,
    NvidiaModelCatalogClient,
    is_nvidia_cloud,
    normalize_nvidia_base_url,
)
from agentos.provider_catalog.service import ProviderModelCatalogService


def test_nvidia_constants_and_provider_sets() -> None:
    assert NVIDIA_CLOUD_BASE_URL == "https://integrate.api.nvidia.com/v1"
    assert DEFAULT_NVIDIA_LOCAL_BASE_URL == "http://localhost:8000/v1"
    assert DEFAULT_NVIDIA_BASE_URL == NVIDIA_CLOUD_BASE_URL
    assert "nvidia" in PROVIDERS_WITH_BASE_URL
    assert "nvidia" in PROVIDERS_WITH_OPTIONAL_KEY


def test_normalize_nvidia_base_url_defaults_empty_to_cloud() -> None:
    assert normalize_nvidia_base_url("") == "https://integrate.api.nvidia.com/v1"
    assert normalize_nvidia_base_url("   ") == "https://integrate.api.nvidia.com/v1"


def test_normalize_nvidia_base_url_appends_v1_when_path_is_root() -> None:
    assert normalize_nvidia_base_url("https://integrate.api.nvidia.com") == "https://integrate.api.nvidia.com/v1"
    assert normalize_nvidia_base_url("http://localhost:8000/") == "http://localhost:8000/v1"


def test_normalize_nvidia_base_url_preserves_custom_subpath() -> None:
    assert normalize_nvidia_base_url("https://nim.internal.corp/api/v1/") == "https://nim.internal.corp/api/v1"


def test_normalize_nvidia_base_url_rejects_invalid_urls_and_insecure_nvidia_cloud() -> None:
    with pytest.raises(ValueError, match="http or https"):
        normalize_nvidia_base_url("ftp://integrate.api.nvidia.com/v1")
    with pytest.raises(ValueError, match="credentials"):
        normalize_nvidia_base_url("https://user:pass@integrate.api.nvidia.com/v1")
    with pytest.raises(ValueError, match="query or fragment"):
        normalize_nvidia_base_url("https://integrate.api.nvidia.com/v1?foo=bar")
    with pytest.raises(ValueError, match="https"):
        normalize_nvidia_base_url("http://integrate.api.nvidia.com/v1")


def test_is_nvidia_cloud_detects_official_nvidia_domains() -> None:
    assert is_nvidia_cloud("https://integrate.api.nvidia.com/v1") is True
    assert is_nvidia_cloud("https://api.nvcf.nvidia.com/v2") is True
    assert is_nvidia_cloud("http://localhost:8000/v1") is False


def test_nvidia_catalog_client_fetches_models_and_sends_bearer_token() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "meta/llama-3.1-70b-instruct", "owned_by": "meta", "context_length": 131072},
                    {"id": "deepseek-ai/deepseek-r1", "owned_by": "deepseek-ai"},
                ]
            },
        )

    client = NvidiaModelCatalogClient(transport=httpx.MockTransport(handler))
    payload = client.fetch_models("nvapi-test-key", base_url="https://integrate.api.nvidia.com/v1")

    assert seen_headers.get("authorization") == "Bearer nvapi-test-key"
    assert len(payload["data"]) == 2


def test_nvidia_catalog_client_raises_auth_error_on_401_402_403() -> None:
    for status in (401, 402, 403):
        client = NvidiaModelCatalogClient(
            transport=httpx.MockTransport(lambda req, s=status: httpx.Response(s, json={"detail": "denied"}))
        )
        with pytest.raises(NvidiaAuthenticationError):
            client.fetch_models("nvapi-bad", base_url="https://integrate.api.nvidia.com/v1")


def test_service_normalizes_nvidia_models_with_reasoning_and_vision_heuristics() -> None:
    service = ProviderModelCatalogService.__new__(ProviderModelCatalogService)
    models = service._normalize(
        "nvidia",
        {
            "data": [
                {"id": "deepseek-ai/deepseek-r1", "context_length": 65536},
                {"id": "meta/llama-3.2-90b-vision-instruct", "context_length": 131072},
                {"id": "nvidia/llama-3.1-nemotron-70b-instruct"},
            ]
        },
    )
    by_id = {m.model_id: m for m in models}
    assert by_id["deepseek-ai/deepseek-r1"].supports_thinking is True
    assert by_id["deepseek-ai/deepseek-r1"].context_window == 65536
    assert by_id["meta/llama-3.2-90b-vision-instruct"].supports_vision is True
    assert by_id["nvidia/llama-3.1-nemotron-70b-instruct"].supports_tools is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/provider_catalog/test_nvidia.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.provider_catalog.nvidia'`.

- [ ] **Step 3: Create `src/agentos/provider_catalog/nvidia.py` and update `models.py`, `service.py`, `resolver_catalog.py`, and `pricing.py`**

Create `src/agentos/provider_catalog/nvidia.py`:
```python
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
```

Update `src/agentos/provider_catalog/models.py`:
- Add `"nvidia"` to `PROVIDERS_WITH_BASE_URL` and `PROVIDERS_WITH_OPTIONAL_KEY`:
```python
PROVIDERS_WITH_BASE_URL: frozenset[str] = frozenset({"ollama", "omniroute", "nvidia"})
PROVIDERS_WITH_OPTIONAL_KEY: frozenset[str] = frozenset({"ollama", "omniroute", "nvidia"})
```

Update `src/agentos/provider_catalog/service.py`:
- Import `DEFAULT_NVIDIA_BASE_URL`, `NvidiaModelCatalogClient`, `is_nvidia_cloud`, `normalize_nvidia_base_url` from `agentos.provider_catalog.nvidia`.
- Accept `nvidia_client: NvidiaModelCatalogClient | None = None` in `ProviderModelCatalogService.__init__` (defaulting to `NvidiaModelCatalogClient()`).
- In `refresh()`, handle `provider_name == "nvidia"` by normalizing `base_url = normalize_nvidia_base_url(str(credential.get("base_url") or DEFAULT_NVIDIA_BASE_URL))`, checking `if is_nvidia_cloud(base_url) and not api_key: raise ValueError("provider API key is required for NVIDIA NIM Cloud")`, and calling `self._nvidia_client.fetch_models(api_key, base_url=base_url)`.
- In `_normalize()`, route `"nvidia"` to `self._normalize_nvidia(raw_payload, refreshed_at)` which parses OpenAI-compatible `data` entries and infers `supports_thinking` (when model ID contains `deepseek-r1`, `qwq`, `thinking`, `nemotron-ultra`, or `reasoning`), `supports_vision` (`vision`, `vl`, `vlm`, `pixtral`, `cosmos`, `neva`, `paligemma`), and `supports_tools=True`.
- Add `"nvidia"` to `_provider(value: str)`.

Update `src/agentos/provider_catalog/resolver_catalog.py`:
- Add `"nvidia"` to the allowed providers set in `PostgresProviderModelCatalog.list_models`:
```python
if provider_name not in {"openai", "anthropic", "google", "groq", "openrouter", "ollama", "omniroute", "opencode", "nvidia"}:
```

Update `src/agentos/provider_catalog/pricing.py`:
- Ensure `_match_pricing` strips vendor prefixes like `meta/`, `nvidia/`, `deepseek-ai/`, `mistralai/`, `google/`, `qwen/` when matching fallback pricing entries, and add common NVIDIA NIM model entries (`llama-3.1-405b`, `llama-3.1-70b`, `llama-3.1-8b`, `nemotron`, `deepseek-r1`, `qwq-32b`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/provider_catalog/test_nvidia.py -v`
Expected: PASS (all tests green).

---

### Task 2: Smart Key Pool (`exhausted` Status, 3-Tier Selection, Manual Activation & Catalog Rotation)

**Files:**
- Modify: `src/agentos/persistence/postgres/provider_api_keys.py`
- Modify: `src/agentos/persistence/postgres/provider_configuration.py`
- Modify: `src/agentos/persistence/postgres/provider_models.py`
- Modify: `src/agentos/provider_catalog/service.py`
- Test: `tests/unit/persistence/test_provider_api_keys.py`
- Test: `tests/unit/persistence/test_provider_configuration_nvidia.py`

- [ ] **Step 1: Write failing tests in `tests/unit/persistence/test_provider_api_keys.py` and `tests/unit/persistence/test_provider_configuration_nvidia.py`**

Append to `tests/unit/persistence/test_provider_api_keys.py`:
```python
def test_mark_exhausted_sets_status_exhausted_and_clears_cooldown() -> None:
    adapter = _adapter()
    key = adapter.add_key(_query(api_key="nvapi-first-key"))
    adapter.mark_cooldown(key["id"], 3600)

    adapter.mark_exhausted(key["id"])

    saved = adapter.list_keys(_query())[0]
    assert saved["status"] == "exhausted"
    assert saved["cooldown_until"] is None


def test_next_available_key_skips_exhausted_keys_in_favor_of_active_or_cooldown() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="nvapi-exhausted-0"))
    second = adapter.add_key(_query(api_key="nvapi-cooldown-1"))
    third = adapter.add_key(_query(api_key="nvapi-active-2"))
    adapter.mark_exhausted(first["id"])
    adapter.mark_cooldown(second["id"], 3600)

    # Tier 1: active key wins
    assert adapter.next_available_key("user-1", "openai").plaintext == "nvapi-active-2"

    # When Tier 1 is excluded, Tier 2 (unexpired cooldown) wins over Tier 3 (exhausted)
    assert (
        adapter.next_available_key("user-1", "openai", exclude=frozenset({third["id"]})).plaintext
        == "nvapi-cooldown-1"
    )

    # When Tier 1 and Tier 2 are excluded, Tier 3 (exhausted) is used as last-resort fallback
    assert (
        adapter.next_available_key("user-1", "openai", exclude=frozenset({second["id"], third["id"]})).plaintext
        == "nvapi-exhausted-0"
    )


def test_activate_key_resets_exhausted_or_cooldown_key_to_active() -> None:
    adapter = _adapter()
    key = adapter.add_key(_query(api_key="nvapi-exhausted-key", label="Free Tier 1"))
    adapter.mark_exhausted(key["id"])

    activated = adapter.activate_key(_query(key_id=key["id"]))

    assert activated["id"] == key["id"]
    assert activated["status"] == "active"
    assert activated["cooldown_until"] is None


def test_activate_key_raises_not_found_for_unknown_key() -> None:
    with pytest.raises(ApplicationNotFoundError):
        _adapter().activate_key(_query(key_id=999))
```

Create `tests/unit/persistence/test_provider_configuration_nvidia.py`:
```python
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.schema import metadata, provider_api_keys, provider_configurations, provider_models
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.nvidia import NvidiaModelCatalogClient


def _setup():
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_configurations, provider_api_keys, provider_models])
    cipher = ProviderSecretCipher(b"0" * 32)
    keys = PostgresProviderApiKeyAdapter(engine, cipher=cipher)
    config = PostgresProviderConfigurationAdapter(engine, cipher=cipher, key_pool=keys)
    return engine, keys, config


def test_nvidia_cloud_requires_api_key_when_no_keys_in_pool() -> None:
    _, _, config = _setup()
    with pytest.raises(ValueError, match="API key"):
        config.configure({"user_id": "u1", "provider": "nvidia", "enabled": True, "api_key": "", "base_url": "https://integrate.api.nvidia.com/v1"})


def test_nvidia_self_hosted_allows_empty_api_key() -> None:
    _, _, config = _setup()
    saved = config.configure({"user_id": "u1", "provider": "nvidia", "enabled": True, "api_key": "", "base_url": "http://localhost:8000/v1"})
    assert saved["provider"] == "nvidia"
    assert saved["base_url"] == "http://localhost:8000/v1"
    assert saved["has_secret"] is False


def test_configuring_provider_with_empty_api_key_preserves_existing_pool_keys() -> None:
    _, keys, config = _setup()
    keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-pool-key-1", "label": "Key 1"})
    keys.add_key({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-pool-key-2", "label": "Key 2"})

    saved = config.configure({"user_id": "u1", "provider": "nvidia", "enabled": True, "api_key": "", "base_url": "https://integrate.api.nvidia.com/v1"})

    assert saved["has_secret"] is True
    remaining = keys.list_keys({"user_id": "u1", "provider": "nvidia"})
    assert len(remaining) == 2
    assert [r["label"] for r in remaining] == ["Key 1", "Key 2"]


def test_test_connection_for_nvidia_returns_reachable_and_model_count() -> None:
    engine, keys, _ = _setup()
    cipher = ProviderSecretCipher(b"0" * 32)
    nvidia_client = NvidiaModelCatalogClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": "meta/llama-3.1-70b-instruct"}]}))
    )
    config = PostgresProviderConfigurationAdapter(engine, cipher=cipher, key_pool=keys, nvidia_client=nvidia_client)

    result = config.test_connection({"user_id": "u1", "provider": "nvidia", "api_key": "nvapi-valid-key", "base_url": "https://integrate.api.nvidia.com/v1"})
    assert result == {"reachable": True, "model_count": 1, "authenticated": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/persistence/test_provider_api_keys.py tests/unit/persistence/test_provider_configuration_nvidia.py -v`
Expected: FAIL (`AttributeError: 'PostgresProviderApiKeyAdapter' object has no attribute 'mark_exhausted'`, `"nvidia"` not accepted in `PostgresProviderConfigurationAdapter`).

- [ ] **Step 3: Implement `mark_exhausted`, `activate_key`, 3-tier `next_available_key`, NVIDIA configuration support, and catalog refresh key rotation**

In `src/agentos/persistence/postgres/provider_api_keys.py`:
- Add `"nvidia"` to `_provider(value: object) -> str`.
- Add `mark_exhausted(self, key_id: int) -> None`:
```python
    def mark_exhausted(self, key_id: int) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            connection.execute(
                update(provider_api_keys)
                .where(provider_api_keys.c.id == int(key_id))
                .values(status="exhausted", cooldown_until=None, updated_at=now)
            )
```
- Add `activate_key(self, command: dict[str, object]) -> dict[str, object]`:
```python
    def activate_key(self, command: dict[str, object]) -> dict[str, object]:
        user_id = self._user_id(command)
        provider_name = self._provider(command.get("provider"))
        key_id = self._key_id(command.get("key_id"))
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(provider_api_keys)
                .where(
                    provider_api_keys.c.id == key_id,
                    provider_api_keys.c.user_id == user_id,
                    provider_api_keys.c.provider == provider_name,
                )
                .values(status="active", cooldown_until=None, updated_at=now)
            )
            if not updated.rowcount:
                raise ApplicationNotFoundError("provider API key not found")
            row = connection.execute(select(provider_api_keys).where(provider_api_keys.c.id == key_id)).mappings().one()
        return self._serialize(row)
```
- Update `next_available_key` to use 3 tiers:
  1. Tier 1 (`status != "exhausted"` and `(cooldown_until is None or cooldown_until <= now)`)
  2. Tier 2 (`status != "exhausted"` and `cooldown_until > now`)
  3. Tier 3 (`status == "exhausted"`)

In `src/agentos/persistence/postgres/provider_configuration.py`:
- Import `DEFAULT_NVIDIA_BASE_URL`, `NvidiaAuthenticationError`, `NvidiaModelCatalogClient`, `is_nvidia_cloud`, `normalize_nvidia_base_url` from `agentos.provider_catalog.nvidia`.
- Accept `nvidia_client: NvidiaModelCatalogClient | None = None` in `__init__`.
- Update `configure`:
  - Check existing keys via `existing_keys = self._key_pool.list_keys({"user_id": user_id, "provider": provider_name})`.
  - For `"nvidia"`, require an API key (`api_key` or `existing_keys`) when `is_nvidia_cloud(base_url)` is True; allow empty key when self-hosted (`is_nvidia_cloud(base_url)` is False).
  - For any provider, if `api_key` is empty and `existing_keys` is non-empty, preserve the existing pool keys instead of calling `clear_primary_key` or raising `"provider API key is required"`.
- Update `test_connection` to support `provider_name == "nvidia"` using `self._nvidia_client.fetch_models(api_key, base_url=base_url)` and translating `NvidiaAuthenticationError` into `{"reachable": True, "model_count": 0, "authenticated": False}`.

In `src/agentos/persistence/postgres/provider_models.py` and `src/agentos/provider_catalog/service.py`:
- Update `ProviderModelCatalogService.refresh` so if `self._repository` exposes key pool rotation helpers (`next_available_key`, `mark_exhausted`, `mark_cooldown`, `mark_active`) or when `credential()` returns a key that fails with HTTP 401/402/403/429 or `NvidiaAuthenticationError`/`OpenCodeAuthenticationError`, it marks the key (`mark_exhausted` on 401/402/403, `mark_cooldown` on 429) and retries with the next key in the pool until a key succeeds or all keys are tried.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/persistence/test_provider_api_keys.py tests/unit/persistence/test_provider_configuration_nvidia.py -v`
Expected: PASS.

---

### Task 3: Stream Out-of-Usage Detection & Ecosystem-Wide Rotation (`provider_stream`, `provider_key_fallback`, `chat.py`, `effort.py`, `http.py`, `production.py`, `selection.py`)

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py`
- Modify: `src/agentos/agentic/provider_key_fallback.py`
- Modify: `src/agentos/agentic/effort.py`
- Modify: `src/agentos/workers/chat.py`
- Modify: `src/agentos/providers/http.py`
- Modify: `src/agentos/bootstrap/production.py`
- Modify: `src/agentos/cli_code/selection.py`
- Test: `tests/unit/agentic/test_multi_key_provider_stream_transport.py`
- Test: `tests/unit/workers/test_chat_provider_transport.py`

- [ ] **Step 1: Write failing tests in `tests/unit/agentic/test_multi_key_provider_stream_transport.py` and `tests/unit/workers/test_chat_provider_transport.py`**

Add tests in `tests/unit/agentic/test_multi_key_provider_stream_transport.py` for:
1. `ProviderKeyRejected(exhausted=True)` calls `pool.mark_exhausted(key.id)` instead of `pool.mark_cooldown` and rotates to key 2.
2. `ProviderKeyRejected(exhausted=False, retry_after_seconds=120)` calls `pool.mark_cooldown(key.id, 120)` bounded to `[1, 3600]`.
3. `HTTPProviderStreamTransport` raises `ProviderKeyRejected(exhausted=True)` on HTTP `402`, `401`, `403`, and `429`/`400` when the response body mentions `"insufficient_quota"`, `"credit balance"`, or `"out of credits"`.
4. `HTTPProviderStreamTransport` raises `ProviderKeyRejected(exhausted=False, retry_after_seconds=15)` on HTTP `429` with `Retry-After: 15` and generic rate limit message.
5. `MultiKeyProviderStreamTransport` rotates when the very first SSE item from `inner.stream` is `NormalizedStreamItem(StreamKind.ERROR, ...)` with `error_code in ("RATE_LIMITED", "QUOTA_EXCEEDED", "AUTHENTICATION")` before any content token is yielded.

Add tests in `tests/unit/workers/test_chat_provider_transport.py` for:
1. `worker._provider_transport({"provider": "nvidia", "user_id": "user-1", "model_id": "meta/llama-3.1-70b-instruct"})` uses `https://integrate.api.nvidia.com/v1` and wraps in `MultiKeyProviderStreamTransport`.
2. `worker._transport_for(turn, "nvidia")` (used by subagents and vision reading) also returns a `MultiKeyProviderStreamTransport` that rotates keys when key 0 is rejected.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agentic/test_multi_key_provider_stream_transport.py tests/unit/workers/test_chat_provider_transport.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement out-of-usage detection and ecosystem-wide key rotation**

In `src/agentos/agentic/provider_stream.py`:
- Update `ProviderKeyRejected`:
```python
class ProviderKeyRejected(RuntimeError):
    """Raised when the provider rejects the API key before any stream output is produced."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        exhausted: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.exhausted = exhausted
        self.retry_after_seconds = retry_after_seconds
```
- Add helper `_classify_http_rejection(response: httpx.Response) -> ProviderKeyRejected | None`:
  - Extract response text safely (`_safe_response_text(response)`).
  - Check quota/exhaustion markers (`"insufficient_quota"`, `"quota exceeded"`, `"out of credits"`, `"credit balance"`, `"billing_hard_limit_reached"`, `"exhausted"`, `"payment required"`, `"not enough credits"`).
  - If `status_code in (401, 402, 403)`: return `ProviderKeyRejected(..., status_code=status_code, exhausted=True)`.
  - If `status_code == 429`: if quota marker matches, return `ProviderKeyRejected(..., status_code=429, exhausted=True)`; else parse `Retry-After` integer header bounded to `[1, 3600]` and return `ProviderKeyRejected(..., status_code=429, exhausted=False, retry_after_seconds=retry_after)`.
  - If `status_code in (400, 422)` and quota marker matches: return `ProviderKeyRejected(..., status_code=status_code, exhausted=True)`.
  - Otherwise return `None`.
- In `HTTPProviderStreamTransport.stream`: use `httpx.Client.stream(...)` or catch `httpx.HTTPStatusError` and raise the classified `ProviderKeyRejected`. Also, if the very first normalized SSE event (when `sequence == 0` / before any non-error item is yielded) is `StreamKind.ERROR` with quota/auth/rate-limit error code or message, raise `ProviderKeyRejected` so `MultiKeyProviderStreamTransport` rotates transparently.

In `src/agentos/agentic/provider_key_fallback.py`:
- Extend `ProviderKeyPool` protocol with `def mark_exhausted(self, key_id: int) -> None: ...` (falling back gracefully via `getattr` if a test pool doesn't implement it).
- When `ProviderKeyRejected` is caught and `not emitted`:
  - If `rejected.exhausted` and hasattr(self._key_pool, "mark_exhausted"): call `self._key_pool.mark_exhausted(credential.id)`
  - Else: compute `cooldown = max(1, min(int(rejected.retry_after_seconds), 3600))` if `rejected.retry_after_seconds is not None` else `self._cooldown_seconds`, and call `self._key_pool.mark_cooldown(credential.id, cooldown)`.

In `src/agentos/workers/chat.py`, `src/agentos/agentic/effort.py`, `src/agentos/providers/http.py`, `src/agentos/bootstrap/production.py`, `src/agentos/cli_code/selection.py`:
- Add `"nvidia": "https://integrate.api.nvidia.com/v1"` to `PROVIDER_BASE_URLS`.
- Update `ChatWorker._base_url_for` to resolve `"nvidia"` via `normalize_nvidia_base_url`.
- Update `ChatWorker._transport_for` to use the same `MultiKeyProviderStreamTransport` pool wrapper as `_provider_transport` so subagents and vision reading automatically rotate keys.
- Add `"nvidia"` to `effort.py` (`_OPENAI_STYLE_PROVIDERS`), `providers/http.py`, `bootstrap/production.py`, and `cli_code/selection.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/agentic/test_multi_key_provider_stream_transport.py tests/unit/workers/test_chat_provider_transport.py -v`
Expected: PASS.

---

### Task 4: API Gateway Endpoints (`POST /v1/providers/nvidia/test` & `POST /v1/providers/{provider}/keys/{key_id}:activate`)

**Files:**
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/api/contracts.py`
- Test: `tests/unit/api/test_provider_keys_gateway.py`

- [ ] **Step 1: Write failing tests in `tests/unit/api/test_provider_keys_gateway.py`**

Add tests in `tests/unit/api/test_provider_keys_gateway.py`:
1. `test_activating_an_exhausted_key_resets_its_status_to_active`: adds a key for `"nvidia"`, marks it exhausted, calls `POST /v1/providers/nvidia/keys/{id}:activate`, asserts HTTP 200 and `status == "active"`.
2. `test_testing_nvidia_connection_routes_through_gateway`: calls `POST /v1/providers/nvidia/test` with `{"api_key": "nvapi-12345678", "base_url": "https://integrate.api.nvidia.com/v1"}` and asserts HTTP 200 with `reachable`, `model_count`, `authenticated`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/api/test_provider_keys_gateway.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement gateway routes and contracts**

In `src/agentos/api/contracts.py`:
- Add `def activate_key(self, command: dict[str, object]) -> dict[str, object]: ...` to `ProviderApiKeyPort`.

In `src/agentos/api/gateway.py`:
- Add `"nvidia"` to `_provider_name(provider: str) -> str`.
- Add `@router.post("/v1/providers/nvidia/test")` calling `provider_configuration.test_connection({"user_id": ..., "provider": "nvidia", "api_key": ..., "base_url": ...})`.
- Add `@router.post("/v1/providers/{provider}/keys/{key_id}:activate")` calling `provider_api_keys.activate_key({"user_id": ..., "provider": provider_name, "key_id": key_id})`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/api/test_provider_keys_gateway.py -v`
Expected: PASS.

---

### Task 5: Frontend NVIDIA Setup, Brand Metadata & Exhausted Key Reactivation UI

**Files:**
- Create: `frontend/src/assets/providers/nvidia.svg`
- Create: `frontend/src/features/providers/NvidiaSetup.tsx`
- Modify: `frontend/src/api/providers.ts`
- Modify: `frontend/src/features/providers/providerBrand.ts`
- Modify: `frontend/src/features/providers/ProviderKeyList.tsx`
- Modify: `frontend/src/features/providers/useProviderKeysState.ts`
- Modify: `frontend/src/features/providers/useProviderState.ts`
- Modify: `frontend/src/features/providers/ProviderDetail.tsx`
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`
- Modify: `frontend/src/features/providers/VisionModelSetting.tsx`
- Modify: `frontend/src/features/conversations/CodeSessionProposalCard.tsx`
- Test: `frontend/src/features/providers/__tests__/ProviderKeyList.test.tsx`
- Test: `frontend/src/features/providers/__tests__/NvidiaSetup.test.tsx`

- [ ] **Step 1: Write failing frontend tests (`ProviderKeyList.test.tsx` and `NvidiaSetup.test.tsx`)**

Create `frontend/src/features/providers/__tests__/ProviderKeyList.test.tsx` and `NvidiaSetup.test.tsx` testing:
1. `ProviderKeyList` renders `"Sem saldo / Esgotada"` badge when `key.status === "exhausted"` and triggers `onActivate(key.id)` when clicking `"Reativar"`.
2. `NvidiaSetup` renders mode toggle (`NVIDIA NIM Cloud` vs `Self-Hosted NIM`), shows `https://integrate.api.nvidia.com/v1` default URL, and triggers `onTestConnection` / `onSave`.

- [ ] **Step 2: Run frontend tests to verify they fail**

Run: `npm --prefix frontend test -- --run src/features/providers/__tests__/ProviderKeyList.test.tsx src/features/providers/__tests__/NvidiaSetup.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement frontend API methods, NVIDIA branding, `NvidiaSetup.tsx`, and `ProviderKeyList.tsx` reactivation UI**

1. Create `frontend/src/assets/providers/nvidia.svg` (clean SVG mark in `#76B900`).
2. Update `frontend/src/api/providers.ts`:
   - Widen `ProviderApiKey.status` to `'active' | 'cooldown' | 'exhausted'`.
   - Add `activateProviderApiKey(provider: string, keyId: number): Promise<ProviderApiKey>` (`POST /v1/providers/${provider}/keys/${keyId}:activate`).
   - Add `testNvidiaConnection(input: { apiKey?: string; baseUrl?: string }): Promise<NvidiaTestResult>` (`POST /v1/providers/nvidia/test`).
3. Update `frontend/src/features/providers/providerBrand.ts`:
   - Add `nvidia` to `PROVIDER_ORDER` and `BRANDS` (`name: 'NVIDIA NIM'`, `accent: '#76B900'`, `logoUrl: nvidiaLogo`, `keyPlaceholder: 'nvapi-...'`, `docsUrl: 'https://build.nvidia.com'`, `supportsBaseUrl: true`, `keyOptional: true`).
4. Create `frontend/src/features/providers/NvidiaSetup.tsx`:
   - Mode toggle between **NVIDIA NIM Cloud** (`https://integrate.api.nvidia.com/v1`) and **Self-Hosted NIM** (`http://localhost:8000/v1` or custom URL).
   - Guidance callout explaining free credits on `build.nvidia.com` and automatic multi-key rotation when a key runs out of credits or hits rate limits.
   - Test connection button showing reachability, authentication status, and discovered model count.
5. Update `frontend/src/features/providers/ProviderKeyList.tsx` and `useProviderKeysState.ts`:
   - Add `activate(keyId)` action to `useProviderKeysState`.
   - Render `status === 'exhausted'` badge (`Sem saldo / Esgotada`) and `"Reativar"` button for keys in `exhausted` or `cooldown` state.
6. Wire `NvidiaSetup` and `nvidia` label in `ProviderDetail.tsx`, `ProviderSettingsPage.tsx`, `useProviderState.ts`, `VisionModelSetting.tsx`, and `CodeSessionProposalCard.tsx`.

- [ ] **Step 4: Run frontend tests to verify they pass**

Run: `npm --prefix frontend test -- --run src/features/providers/__tests__/ProviderKeyList.test.tsx src/features/providers/__tests__/NvidiaSetup.test.tsx`
Expected: PASS.

---

### Task 6: Adversarial Testing, Full Suite Verification & Real Browser Playwright Inspection

**Files:**
- Modify: `tests/unit/provider_catalog/test_nvidia.py` (adversarial edge cases)
- Modify: `tests/unit/agentic/test_multi_key_provider_stream_transport.py` (adversarial edge cases)

- [ ] **Step 1: Run adversarial backend & frontend tests**

Verify all adversarial scenarios pass:
- Malformed `Retry-After` headers (`"abc"`, `"-99"`, `"99999999"`) do not crash and clamp to `[1, 3600]`.
- Mid-stream failure after >=1 content token is emitted does **not** rotate keys or duplicate partial output.
- All keys exhausted falls back to Tier 3 and surfaces a sanitized error without leaking `nvapi-...` plaintext in error messages or logs.
- Saving provider settings with empty `api_key` when pool keys exist preserves all pool keys.

Run:
- `pytest tests/unit/provider_catalog/test_nvidia.py tests/unit/persistence/test_provider_api_keys.py tests/unit/persistence/test_provider_configuration_nvidia.py tests/unit/agentic/test_multi_key_provider_stream_transport.py tests/unit/workers/test_chat_provider_transport.py tests/unit/api/test_provider_keys_gateway.py -v`
- `npm --prefix frontend test -- --run`
- `npm --prefix frontend run build`

- [ ] **Step 2: Visual Verification in Real Browser (Playwright)**

Start backend/frontend dev servers, open the app in Playwright browser, and visually inspect:
1. Providers Grid showing the **NVIDIA NIM** card with `#76B900` branding and status badge.
2. NVIDIA Provider Detail drawer / modal in both **NVIDIA NIM Cloud** and **Self-Hosted NIM** modes (desktop & mobile viewports).
3. Multi-key pool (`ProviderKeyList`) displaying `Principal`, `Cooldown`, and `Sem saldo / Esgotada` states and testing the `"Reativar"` button live.
