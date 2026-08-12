# Ollama Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ollama` as Orin's fifth provider, serving both a local Ollama instance and Ollama Cloud from one configurable provider row.

**Architecture:** One `(user_id, "ollama")` row in `provider_configurations`, with the Local/Cloud mode derived from the configured host rather than a new column. The turn streams over Ollama's **native** `/api/chat` (NDJSON) rather than its OpenAI-compatible endpoint, because only the native API accepts `options.num_ctx` — without it a local model stays pinned at Ollama's 4096-token default, far below what the agentic loop's system prompt and tool schemas need. The model catalog is built from `GET /api/tags` plus a `POST /api/show` per model, which is the only source of each model's real context window and of whether it supports tools.

**Tech Stack:** Python 3.13, httpx, SQLAlchemy, FastAPI, pytest; React 19 + TypeScript + Vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-08-12-ollama-provider-design.md`

**Run tests with `.venv/Scripts/python.exe -m pytest`, not `uv run`.** On this machine `uv run` tries to re-sync the venv and fails on `.pyd` files held open by the running Orin runtime, which leaves the venv broken.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `src/agentos/provider_catalog/ollama.py` | Base URL normalization, Local/Cloud discrimination, and the `/api/tags` + `/api/show` catalog client |
| `tests/unit/provider_catalog/test_ollama.py` | Unit tests for the above |

**Modified**

| File | Change |
|---|---|
| `src/agentos/provider_catalog/models.py` | Single home for the two provider-shape rules the other four layers consult |
| `src/agentos/provider_catalog/ports.py` | `ProviderCatalogUpstream.fetch` declares `base_url` |
| `src/agentos/provider_catalog/http.py` | `OpenRouterModelCatalogClient.fetch` accepts and ignores `base_url` |
| `src/agentos/provider_catalog/service.py` | `_normalize_ollama`, normalizer dispatch table, allowlist, single unbranched `fetch` call |
| `src/agentos/bootstrap/production.py` | Register `OllamaCatalogClient` |
| `src/agentos/agentic/provider_stream.py` | Extract per-provider request builders; add `normalize_ndjson` and `_ollama_request`; `num_ctx` constructor argument |
| `src/agentos/workers/chat.py` | Base URL and credential resolution for Ollama; compute `num_ctx` |
| `src/agentos/persistence/postgres/provider_configuration.py` | Cloud-aware key rule, Ollama base URL, `test_connection` dispatch |
| `src/agentos/api/gateway.py` | Allowlist, key-required rule, `POST /v1/providers/ollama/test` |
| `src/agentos/provider_catalog/resolver_catalog.py` | Add `ollama` and `omniroute` to the provider tuple |
| `frontend/src/api/providers.ts` | `PROVIDER_NAMES`, `testOllamaConnection` |
| `frontend/src/features/providers/ProviderSettingsPage.tsx` | Label plus the `OllamaSetup` panel |
| `frontend/src/components/ModelPicker.tsx` | Mark and demote models without tool support |

---

## Task 1: Ollama base URL normalization

**Files:**
- Create: `src/agentos/provider_catalog/ollama.py`
- Test: `tests/unit/provider_catalog/test_ollama.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/provider_catalog/test_ollama.py`:

```python
from __future__ import annotations

import pytest

from agentos.provider_catalog.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    OLLAMA_CLOUD_BASE_URL,
    is_ollama_cloud,
    normalize_ollama_base_url,
)


def test_normalizes_the_local_default_without_leaking_credentials() -> None:
    assert normalize_ollama_base_url("http://localhost:11434/") == "http://localhost:11434"
    assert normalize_ollama_base_url(DEFAULT_OLLAMA_BASE_URL) == DEFAULT_OLLAMA_BASE_URL
    with pytest.raises(ValueError):
        normalize_ollama_base_url("ftp://localhost:11434")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("http://key@localhost:11434")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("http://localhost:11434?token=secret")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("   ")


def test_strips_an_api_or_v1_suffix_the_user_may_have_pasted() -> None:
    """The native API lives at /api/*, so the base URL must be the bare origin."""
    assert normalize_ollama_base_url("http://localhost:11434/v1") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/api/") == "http://localhost:11434"
    assert normalize_ollama_base_url("https://gpu.lan/ollama/v1") == "https://gpu.lan/ollama"


def test_cloud_is_recognized_by_host_not_by_a_stored_mode_flag() -> None:
    assert is_ollama_cloud(OLLAMA_CLOUD_BASE_URL) is True
    assert is_ollama_cloud("https://api.ollama.com") is True
    assert is_ollama_cloud(DEFAULT_OLLAMA_BASE_URL) is False
    assert is_ollama_cloud("http://gpu.lan:11434") is False
    assert is_ollama_cloud("https://notollama.com") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_ollama.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.provider_catalog.ollama'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agentos/provider_catalog/ollama.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_ollama.py -v`

Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/ollama.py tests/unit/provider_catalog/test_ollama.py
git commit -m "feat(provider-catalog): resolve an Ollama base URL and its mode"
```

---

## Task 2: The Ollama catalog client

**Files:**
- Modify: `src/agentos/provider_catalog/ollama.py`
- Test: `tests/unit/provider_catalog/test_ollama.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/provider_catalog/test_ollama.py` (and add `import httpx` plus `OllamaCatalogClient` to the existing imports at the top of the file):

```python
def _handler(seen: list[str], *, show_fails_for: str | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3:8b", "model": "qwen3:8b", "details": {"family": "qwen3"}},
                {"name": "llava:7b", "model": "llava:7b", "details": {"family": "llava"}},
            ]})
        import json as _json

        model = _json.loads(request.content)["model"]
        if model == show_fails_for:
            return httpx.Response(500, json={"error": "boom"})
        if model == "llava:7b":
            return httpx.Response(200, json={
                "capabilities": ["completion", "vision"],
                "model_info": {"llava.context_length": 32768, "llava.block_count": 32},
            })
        return httpx.Response(200, json={
            "capabilities": ["completion", "tools"],
            "model_info": {"qwen3.context_length": 262144, "general.parameter_count": 8},
        })

    return handle


def test_merges_the_tag_list_with_per_model_details() -> None:
    seen: list[str] = []
    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(_handler(seen))))

    models = client.fetch("", base_url="http://localhost:11434")

    assert seen[0] == "GET http://localhost:11434/api/tags"
    assert [item["id"] for item in models] == ["qwen3:8b", "llava:7b"]
    assert models[0]["context_length"] == 262144
    assert models[0]["capabilities"] == ["completion", "tools"]
    assert models[1]["context_length"] == 32768
    assert models[1]["capabilities"] == ["completion", "vision"]


def test_sends_a_bearer_token_only_when_a_cloud_key_is_configured() -> None:
    headers: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        headers.append(dict(request.headers))
        return httpx.Response(200, json={"models": []})

    transport = httpx.MockTransport(handle)
    OllamaCatalogClient(client=httpx.Client(transport=transport)).fetch("", base_url="http://localhost:11434")
    assert "authorization" not in headers[0]

    OllamaCatalogClient(client=httpx.Client(transport=transport)).fetch("cloud-secret", base_url="https://ollama.com")
    assert headers[1]["authorization"] == "Bearer cloud-secret"


def test_a_failed_detail_lookup_degrades_only_that_model() -> None:
    """One unreadable model must not cost the user the whole catalog refresh."""
    seen: list[str] = []
    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(_handler(seen, show_fails_for="llava:7b"))))

    models = client.fetch("", base_url="http://localhost:11434")

    assert [item["id"] for item in models] == ["qwen3:8b", "llava:7b"]
    assert models[1]["context_length"] is None
    assert models[1]["capabilities"] == []


def test_connection_failure_is_a_sanitized_catalog_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = OllamaCatalogClient(client=httpx.Client(transport=httpx.MockTransport(handle)))

    with pytest.raises(RuntimeError) as failure:
        client.fetch("cloud-secret", base_url="https://ollama.com")

    assert "cloud-secret" not in str(failure.value)
    assert "cloud-secret" not in repr(client)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_ollama.py -v`

Expected: FAIL — `ImportError: cannot import name 'OllamaCatalogClient'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/agentos/provider_catalog/ollama.py` (after `is_ollama_cloud`, and add `from typing import Any` plus `import httpx` to the imports):

```python
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
```

Extend `__all__` to `["DEFAULT_OLLAMA_BASE_URL", "OLLAMA_CLOUD_BASE_URL", "OllamaCatalogClient", "is_ollama_cloud", "normalize_ollama_base_url"]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_ollama.py -v`

Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/ollama.py tests/unit/provider_catalog/test_ollama.py
git commit -m "feat(provider-catalog): read the Ollama model catalog with its details"
```

---

## Task 3: Declare `base_url` on the upstream catalog contract

This removes the provider-name branch in `refresh` instead of adding a third provider to it, and gives the two provider-shape rules a single home. Four modules need those rules — the catalog service, the credential adapter, the worker and the gateway — so defining them in each is three chances for them to drift apart.

**Files:**
- Modify: `src/agentos/provider_catalog/models.py` (append at the end)
- Modify: `src/agentos/provider_catalog/ports.py:16-17`
- Modify: `src/agentos/provider_catalog/http.py:17`
- Modify: `src/agentos/provider_catalog/service.py:38-43`
- Test: `tests/unit/provider_catalog/test_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/provider_catalog/test_service.py`:

```python
def test_every_upstream_is_called_with_the_stored_base_url() -> None:
    """The Protocol declares base_url, so refresh needs no per-provider branch."""
    repository = FakeRepository()
    seen: dict[str, str] = {}

    class RecordingUpstream:
        def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
            seen["base_url"] = base_url
            return []

    service = ProviderModelCatalogService(repository, {"openrouter": RecordingUpstream()}, now=lambda: datetime(2026, 8, 12, tzinfo=UTC))
    service.refresh(context("user-a"), "openrouter")

    assert seen["base_url"] == ""


def test_the_provider_shape_rules_have_a_single_definition() -> None:
    """Four layers consult these; a per-module copy is a drift waiting to happen."""
    from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL, PROVIDERS_WITH_OPTIONAL_KEY

    assert PROVIDERS_WITH_BASE_URL == frozenset({"omniroute", "ollama"})
    assert PROVIDERS_WITH_OPTIONAL_KEY == frozenset({"omniroute", "ollama"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_service.py::test_every_upstream_is_called_with_the_stored_base_url -v`

Expected: FAIL — `KeyError: 'base_url'`, because `refresh` still calls `fetch(api_key)` for any provider that is not `omniroute`; and `ImportError` for the second test.

- [ ] **Step 3: Write minimal implementation**

Append to `src/agentos/provider_catalog/models.py`:

```python
# Providers whose endpoint the user configures rather than the code, and which
# are therefore reachable without a credential of their own. Defined once
# because the catalog service, the credential adapter, the chat worker and the
# gateway all branch on them; Ollama narrows the second set at runtime, since
# only its local mode is keyless.
PROVIDERS_WITH_BASE_URL = frozenset({"omniroute", "ollama"})
PROVIDERS_WITH_OPTIONAL_KEY = frozenset({"omniroute", "ollama"})
```

In `src/agentos/provider_catalog/ports.py`, replace the `ProviderCatalogUpstream` body:

```python
class ProviderCatalogUpstream(Protocol):
    # Declared here rather than left to each client's default, so the service
    # needs no per-provider branch to decide whether the kwarg is accepted.
    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]: ...
```

In `src/agentos/provider_catalog/http.py`, change the signature of `OpenRouterModelCatalogClient.fetch` (OpenRouter has a single fixed host, so the parameter is accepted and ignored):

```python
    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
```

In `src/agentos/provider_catalog/service.py`, replace the branched call in `refresh`:

```python
        try:
            raw_models = upstream.fetch(api_key, base_url=str(credential.get("base_url") or ""))
        except Exception as error:
            raise ProviderCatalogUnavailable("provider catalog refresh failed") from error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog -v`

Expected: PASS — every previously passing test still passes, including `test_refreshes_omniroute_from_its_saved_gateway_url`, plus the new one.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/models.py src/agentos/provider_catalog/ports.py src/agentos/provider_catalog/http.py src/agentos/provider_catalog/service.py tests/unit/provider_catalog/test_service.py
git commit -m "refactor(provider-catalog): declare base_url on the upstream contract"
```

---

## Task 4: Normalize Ollama models into the catalog

**Files:**
- Modify: `src/agentos/provider_catalog/service.py:47`, `:69-73`
- Test: `tests/unit/provider_catalog/test_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/provider_catalog/test_service.py`:

```python
class FakeOllama:
    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
        assert base_url == "http://localhost:11434"
        return [
            {"id": "qwen3:8b", "name": "qwen3:8b", "context_length": 262144, "capabilities": ["completion", "tools"]},
            {"id": "llava:7b", "name": "llava:7b", "context_length": 32768, "capabilities": ["completion", "vision"]},
            {"id": "orphan:1b", "name": "orphan:1b", "context_length": None, "capabilities": []},
        ]


def _ollama_service(repository: FakeRepository) -> ProviderModelCatalogService:
    repository.configured[("user-a", "ollama")] = {
        "enabled": True, "api_key": "", "base_url": "http://localhost:11434",
    }
    return ProviderModelCatalogService(repository, {"ollama": FakeOllama()}, now=lambda: datetime(2026, 8, 12, tzinfo=UTC))


def test_refresh_normalizes_ollama_models_including_their_capabilities() -> None:
    repository = FakeRepository()
    service = _ollama_service(repository)

    receipt = service.refresh(context("user-a"), "ollama")
    items = {item.model_id: item for item in service.list(context("user-a"), "ollama")}

    assert receipt.count == 3
    assert items["qwen3:8b"].provider == "ollama"
    assert items["qwen3:8b"].context_window == 262144
    assert items["qwen3:8b"].capabilities == ("completion", "tools")
    assert items["qwen3:8b"].pricing is None
    assert items["qwen3:8b"].route_kind == "model"


def test_a_vision_capability_becomes_an_image_input_modality() -> None:
    repository = FakeRepository()
    service = _ollama_service(repository)
    service.refresh(context("user-a"), "ollama")

    items = {item.model_id: item for item in service.list(context("user-a"), "ollama")}

    assert items["llava:7b"].input_modalities == ("text", "image")
    assert items["llava:7b"].output_modalities == ("text",)
    assert items["qwen3:8b"].input_modalities == ("text",)


def test_a_model_without_details_is_still_catalogued() -> None:
    repository = FakeRepository()
    service = _ollama_service(repository)
    service.refresh(context("user-a"), "ollama")

    items = {item.model_id: item for item in service.list(context("user-a"), "ollama")}

    assert items["orphan:1b"].context_window is None
    assert items["orphan:1b"].capabilities == ()


def test_a_local_ollama_refreshes_without_any_credential() -> None:
    """Local Ollama needs no key, so an empty one must not block the refresh."""
    repository = FakeRepository()
    service = _ollama_service(repository)

    assert service.refresh(context("user-a"), "ollama").count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_service.py -k ollama -v`

Expected: FAIL — `ProviderCatalogUnavailable: unsupported provider`

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/provider_catalog/service.py`, extend the existing `.models` import to bring in the shared rule defined in Task 3:

```python
from .models import PROVIDERS_WITH_OPTIONAL_KEY, PricingSummary, ProviderCatalogContext, ProviderModelRecord, RefreshReceipt
```

Replace the empty-key guard in `refresh`:

```python
        if not isinstance(api_key, str) or (not api_key and normalized_provider not in PROVIDERS_WITH_OPTIONAL_KEY):
            raise ProviderCatalogUnavailable("provider credential is unavailable")
```

Replace the normalizer selection in `refresh`:

```python
        normalizer = _NORMALIZERS.get(normalized_provider, _normalize_openrouter)
```

Extend the allowlist in `_provider`:

```python
    if normalized not in {"openai", "anthropic", "openrouter", "omniroute", "ollama"}:
        raise ProviderCatalogUnavailable("unsupported provider")
```

Add `_normalize_ollama` after `_normalize_omniroute`:

```python
def _normalize_ollama(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    capabilities = tuple(sorted({_text(value) for value in _list(raw.get("capabilities")) if _text(value)}))
    context_window = raw.get("context_length")
    if isinstance(context_window, bool) or not isinstance(context_window, int) or context_window < 1:
        context_window = None
    return ProviderModelRecord(
        provider="ollama",
        model_id=model_id,
        display_name=_text(raw.get("name")) or model_id,
        context_window=context_window,
        capabilities=capabilities,
        # Ollama reports vision as a capability rather than as a modality list.
        input_modalities=("text", "image") if "vision" in capabilities else ("text",),
        output_modalities=("text",),
        # Local inference is free, and the hosted API publishes no per-token price.
        pricing=None,
        refreshed_at=refreshed_at,
    )
```

Add the dispatch table at the end of the module, after all three normalizers are defined:

```python
_NORMALIZERS = {
    "openrouter": _normalize_openrouter,
    "omniroute": _normalize_omniroute,
    "ollama": _normalize_ollama,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog -v`

Expected: PASS — all tests, including the four new ones.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/service.py tests/unit/provider_catalog/test_service.py
git commit -m "feat(provider-catalog): normalize Ollama models and their capabilities"
```

---

## Task 5: Register the Ollama upstream in production composition

**Files:**
- Modify: `src/agentos/bootstrap/production.py:42`, `:274`
- Test: `tests/unit/provider_catalog/test_ollama.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/provider_catalog/test_ollama.py`:

```python
def test_production_composition_registers_the_ollama_upstream() -> None:
    """A provider absent from the composed upstreams can never refresh."""
    import inspect

    from agentos.bootstrap import production

    source = inspect.getsource(production)
    assert "OllamaCatalogClient" in source
    assert '"ollama": OllamaCatalogClient()' in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_ollama.py::test_production_composition_registers_the_ollama_upstream -v`

Expected: FAIL — `AssertionError` on `"OllamaCatalogClient" in source`

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/bootstrap/production.py`, add the import next to the OmniRoute one (line 42):

```python
from agentos.provider_catalog.ollama import OllamaCatalogClient
```

At line 274, extend the upstream mapping:

```python
        provider_catalog=ProviderModelCatalogService(provider_repository, {"openrouter": OpenRouterModelCatalogClient(), "omniroute": OmniRouteCatalogClient(), "ollama": OllamaCatalogClient()}),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_ollama.py -v`

Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/bootstrap/production.py tests/unit/provider_catalog/test_ollama.py
git commit -m "feat(bootstrap): compose the Ollama catalog upstream"
```

---

## Task 6: Extract the per-provider request builders

Pure refactor — no behavior change. `stream()` gains a third provider next, and it already holds two inline branches across roughly seventy lines.

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py:260-326`
- Test: `tests/unit/agentic/test_provider_stream_payload.py`

- [ ] **Step 1: Run the existing tests to establish the baseline**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_provider_stream_payload.py -v`

Expected: PASS. Record the count — the same tests must still pass unchanged after the refactor, since the point is that nothing observable moves.

- [ ] **Step 2: Rewrite `stream` as dispatch plus a shared tail**

In `src/agentos/agentic/provider_stream.py`, replace the whole `stream` method with these four methods (keep `_with_cached_tail` exactly as it is):

```python
    def _anthropic_request(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        system_items = [item for item in messages if item.get("role") == "system"]
        messages = [item for item in messages if item.get("role") != "system"]
        messages = self._with_cached_tail(messages)
        # Anthropic requires max_tokens, so an uncapped turn still has to
        # name a number here; every other provider simply omits the field.
        payload: dict[str, object] = {"model": self.model, "max_tokens": int(requested) if requested else ANTHROPIC_REQUIRED_MAX_TOKENS, "messages": messages, "stream": True}
        if system_items:
            # The first system item is the fixed agent prompt, byte-identical
            # across every iteration of a turn (and most turns of a
            # conversation) -- that is the part worth caching. Anything after
            # it (a context-budget trim marker, the final-iteration closing
            # instruction) is call-specific and would invalidate a cache
            # entry keyed on the whole joined string, so it stays out of the
            # cached prefix as separate, uncached blocks instead.
            payload["system"] = [
                {"type": "text", "text": str(item.get("content", "")), **({"cache_control": {"type": "ephemeral"}} if index == 0 else {})}
                for index, item in enumerate(system_items)
            ]
        if tools:
            projected = [
                {
                    "name": item.get("name") or item.get("function", {}).get("name"),
                    "description": item.get("description") or item.get("function", {}).get("description", ""),
                    "input_schema": item.get("input_schema") or item.get("function", {}).get("parameters", {}),
                }
                for item in tools
            ]
            projected[-1] = {**projected[-1], "cache_control": {"type": "ephemeral"}}
            payload["tools"] = projected
            if tool_choice is not None:
                payload["tool_choice"] = _anthropic_tool_choice(tool_choice)
        headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        return f"{self.base_url}/messages", headers, payload

    def _openai_request(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        payload: dict[str, object] = {
            "model": self.model, "messages": messages, "stream": True,
            # Without this an OpenAI-compatible stream omits usage entirely
            # and the turn records no tokens at all.
            "stream_options": {"include_usage": True},
        }
        # No cap configured means no cap sent: the provider then allows the
        # model its own maximum, instead of us cutting a long reply short.
        if requested:
            payload["max_tokens"] = int(requested)
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = str(tool_choice)
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return f"{self.base_url}/chat/completions", headers, payload

    def _request_for(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        if self.provider == "anthropic":
            return self._anthropic_request(messages, tools, tool_choice, requested)
        return self._openai_request(messages, tools, tool_choice, requested)

    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        endpoint, headers, payload = self._request_for(
            list(request.get("messages") or []),
            list(request.get("tools") or []),
            request.get("tool_choice"),
            request.get("max_output_tokens"),
        )
        with self._client.stream("POST", endpoint, headers=headers, json=payload) as response:
            response.raise_for_status()
            limit = project_rate_limit_headers(response.headers)
            has_limit = any(value is not None for value in (limit.remaining, limit.reset_after_seconds, limit.limit))
            if has_limit:
                yield NormalizedStreamItem(StreamKind.RATE_LIMIT, 1, rate_limit=limit)
            for item in normalize_sse(response.iter_lines(), provider=self.provider):
                yield replace(item, sequence=item.sequence + (1 if has_limit else 0))
```

- [ ] **Step 3: Run the tests to verify nothing moved**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic -v`

Expected: PASS — the same tests as in Step 1, with no edits to any test file.

- [ ] **Step 4: Commit**

```bash
git add src/agentos/agentic/provider_stream.py
git commit -m "refactor(agentic): split the provider request builders out of stream"
```

---

## Task 7: Normalize Ollama's NDJSON stream

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py`
- Test: `tests/unit/agentic/test_provider_stream_payload.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agentic/test_provider_stream_payload.py` (add `normalize_ndjson`, `StreamKind` and `FinishReason` to the imports: `from agentos.agentic.provider_stream import ANTHROPIC_REQUIRED_MAX_TOKENS, HTTPProviderStreamTransport, StreamKind, normalize_ndjson` and `from agentos.providers.models import FinishReason`):

```python
import json


def test_ndjson_stream_yields_text_then_usage_and_a_finish() -> None:
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "hel"}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": "lo"}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                    "done_reason": "stop", "prompt_eval_count": 31, "eval_count": 7}),
    ]

    items = list(normalize_ndjson(lines))

    assert [item.kind for item in items] == [StreamKind.TEXT, StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert "".join(item.text or "" for item in items) == "hello"
    assert items[2].usage.input_tokens == 31
    assert items[2].usage.output_tokens == 7
    assert items[2].usage.total_tokens == 38
    assert items[3].finish_reason is FinishReason.STOP
    assert [item.sequence for item in items] == [1, 2, 3, 4]


def test_ndjson_gives_each_tool_call_its_own_id() -> None:
    """Ollama sends no call id, and two calls sharing one would be merged."""
    lines = [
        json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]}, "done": False}),
        json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "b.txt"}}}]}, "done": False}),
        json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 2}),
    ]

    items = [item for item in normalize_ndjson(lines) if item.kind is StreamKind.TOOL_CALL]

    assert [item.tool_call_id for item in items] == ["tool-call:1", "tool-call:2"]
    assert [item.tool_name for item in items] == ["read_file", "read_file"]
    assert [json.loads(item.arguments_delta or "{}") for item in items] == [{"path": "a.txt"}, {"path": "b.txt"}]


def test_ndjson_finishes_as_tool_calls_when_the_model_asked_for_one() -> None:
    """Ollama reports done_reason "stop" even for a turn that ends in a call."""
    lines = [
        json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {}}}]}, "done": False}),
        json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 1}),
    ]

    finish = [item for item in normalize_ndjson(lines) if item.kind is StreamKind.FINISH]

    assert finish[0].finish_reason is FinishReason.TOOL_CALLS


def test_ndjson_reports_a_provider_error_without_echoing_it() -> None:
    items = list(normalize_ndjson([json.dumps({"error": "model 'ghost' not found, api_key=leaked"})]))

    assert items[0].kind is StreamKind.ERROR
    assert "leaked" not in items[0].error.message
    assert items[0].error.message == "provider stream failed"


def test_ndjson_survives_a_malformed_line() -> None:
    lines = ["{not json", json.dumps({"message": {"content": "ok"}, "done": False})]

    items = list(normalize_ndjson(lines))

    assert items[0].kind is StreamKind.ERROR
    assert items[0].error.code == "INVALID_NDJSON"
    assert items[1].kind is StreamKind.TEXT


def test_ndjson_ignores_blank_keepalive_lines() -> None:
    items = list(normalize_ndjson(["", "   ", json.dumps({"message": {"content": "ok"}, "done": False})]))

    assert [item.kind for item in items] == [StreamKind.TEXT]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_provider_stream_payload.py -k ndjson -v`

Expected: FAIL — `ImportError: cannot import name 'normalize_ndjson'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/agentos/agentic/provider_stream.py`, immediately after `normalize_sse`:

```python
def normalize_ndjson(lines: Iterable[str | bytes]) -> Iterator[NormalizedStreamItem]:
    """Normalize Ollama's native ``/api/chat`` stream: one JSON object per line.

    The native API is used instead of Ollama's OpenAI-compatible endpoint
    because only it accepts ``options.num_ctx``; the compatible one leaves a
    local model pinned at Ollama's 4096-token default.  The cost is this
    second normalizer, since the native stream is NDJSON rather than SSE.
    """
    sequence = 0
    tool_calls = 0
    saw_tool_call = False
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)).strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.ERROR, sequence, error=ProviderError(ProviderErrorCategory.INVALID_RESPONSE, "INVALID_NDJSON", "provider stream failed"))
            continue
        if not isinstance(payload, Mapping):
            continue
        if payload.get("error") is not None:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.ERROR, sequence, error=_safe_error(payload))
            continue
        message = payload.get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str) and content:
                sequence += 1
                yield NormalizedStreamItem(StreamKind.TEXT, sequence, text=content)
            for item in message.get("tool_calls") or ():
                if not isinstance(item, Mapping):
                    continue
                function = item.get("function") if isinstance(item.get("function"), Mapping) else {}
                # Ollama emits no call id, and each call arrives complete in a
                # single chunk rather than as argument deltas. A stream-scoped
                # counter is what keeps two distinct calls from colliding on
                # one id and being merged into one by the runtime.
                tool_calls += 1
                saw_tool_call = True
                sequence += 1
                yield NormalizedStreamItem(
                    StreamKind.TOOL_CALL, sequence,
                    tool_call_id=f"tool-call:{tool_calls}",
                    tool_name=str(function.get("name") or "") or None,
                    arguments_delta=json.dumps(function.get("arguments") or {}),
                )
        if payload.get("done") is True:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.USAGE, sequence, usage=_usage({
                "prompt_tokens": payload.get("prompt_eval_count"),
                "completion_tokens": payload.get("eval_count"),
            }))
            sequence += 1
            # Ollama reports "stop" even when the turn ends in a tool call, so
            # the observed calls decide the reason rather than done_reason.
            yield NormalizedStreamItem(StreamKind.FINISH, sequence, finish_reason=FinishReason.TOOL_CALLS if saw_tool_call else _finish(payload.get("done_reason")))
```

Add `normalize_ndjson` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_provider_stream_payload.py -v`

Expected: PASS — the six new tests plus every pre-existing one.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/provider_stream.py tests/unit/agentic/test_provider_stream_payload.py
git commit -m "feat(agentic): normalize Ollama's native NDJSON stream"
```

---

## Task 8: The Ollama request builder

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py`
- Test: `tests/unit/agentic/test_provider_stream_payload.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agentic/test_provider_stream_payload.py`:

```python
def _ollama_transport(captured: list[dict], *, num_ctx: int | None = 32_768, api_key: str = "") -> HTTPProviderStreamTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx.Response(200, text=json.dumps({"done": True, "done_reason": "stop"}) + "\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HTTPProviderStreamTransport(provider="ollama", base_url="http://localhost:11434", api_key=api_key, model="qwen3:8b", client=client, num_ctx=num_ctx)


def test_ollama_posts_to_the_native_chat_endpoint_with_the_context_size() -> None:
    captured: list[dict] = []

    list(_ollama_transport(captured).stream(_request()))

    assert captured[0]["url"] == "http://localhost:11434/api/chat"
    assert captured[0]["body"]["stream"] is True
    assert captured[0]["body"]["options"]["num_ctx"] == 32_768
    assert captured[0]["body"]["options"]["num_predict"] == 512
    # The system prompt stays inline; the native API has no separate field.
    assert captured[0]["body"]["messages"][0] == {"role": "system", "content": "you are orin"}


def test_ollama_omits_the_options_it_was_not_given() -> None:
    captured: list[dict] = []
    request = {key: value for key, value in _request().items() if key != "max_output_tokens"}

    list(_ollama_transport(captured, num_ctx=None).stream(request))

    assert "options" not in captured[0]["body"]


def test_ollama_forwards_the_tool_declarations_unchanged() -> None:
    """Orin already emits the exact shape Ollama's native API expects."""
    captured: list[dict] = []

    list(_ollama_transport(captured).stream(_request()))

    assert captured[0]["body"]["tools"] == _request()["tools"]
    assert "tool_choice" not in captured[0]["body"]


def test_ollama_withholds_the_tools_on_the_closing_iteration() -> None:
    """Ollama has no tool_choice, so "none" is honored by sending no tools."""
    captured: list[dict] = []

    list(_ollama_transport(captured).stream({**_request(), "tool_choice": "none"}))

    assert "tools" not in captured[0]["body"]


def test_ollama_keeps_the_tools_when_tool_choice_is_absent() -> None:
    """A None tool_choice must not be mistaken for the literal string "none"."""
    captured: list[dict] = []

    list(_ollama_transport(captured).stream({**_request(), "tool_choice": None}))

    assert captured[0]["body"]["tools"] == _request()["tools"]


def test_ollama_authenticates_only_when_a_cloud_key_is_configured() -> None:
    local: list[dict] = []
    list(_ollama_transport(local).stream(_request()))
    assert "authorization" not in local[0]["headers"]

    cloud: list[dict] = []
    list(_ollama_transport(cloud, api_key="cloud-secret").stream(_request()))
    assert cloud[0]["headers"]["authorization"] == "Bearer cloud-secret"


def test_ollama_stream_is_normalized_as_ndjson() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join([
            json.dumps({"message": {"content": "hi"}, "done": False}),
            json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 4, "eval_count": 1}),
        ]))

    transport = HTTPProviderStreamTransport(
        provider="ollama", base_url="http://localhost:11434", api_key="", model="qwen3:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)), num_ctx=8192,
    )

    items = list(transport.stream(_request()))

    assert [item.kind for item in items] == [StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert items[0].text == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_provider_stream_payload.py -k ollama -v`

Expected: FAIL — `TypeError: HTTPProviderStreamTransport.__init__() got an unexpected keyword argument 'num_ctx'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/agentic/provider_stream.py`, extend `__init__` (keep `__repr__` unchanged):

```python
    def __init__(self, *, provider: str, base_url: str, api_key: str, model: str, client: httpx.Client | None = None, num_ctx: int | None = None) -> None:
        self.provider, self.base_url, self.model = provider, base_url.rstrip("/"), model
        self._api_key = api_key
        self._num_ctx = num_ctx
        self._client = client or httpx.Client(timeout=60)
        self._owns_client = client is None
```

Add `_ollama_request` after `_openai_request`:

```python
    def _ollama_request(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        payload: dict[str, object] = {"model": self.model, "messages": messages, "stream": True}
        options: dict[str, object] = {}
        # Ollama defaults to a 4096-token window regardless of what the model
        # can hold, which is well under this loop's system prompt plus tool
        # schemas. num_ctx is the whole reason the native API is used here.
        if self._num_ctx:
            options["num_ctx"] = int(self._num_ctx)
        if requested:
            options["num_predict"] = int(requested)
        if options:
            payload["options"] = options
        # The native API has no tool_choice. The runtime's closing "none"
        # iteration is honored by withholding the declarations entirely --
        # stricter than the hint every other provider gets.
        withheld = isinstance(tool_choice, str) and tool_choice.lower() == "none"
        if tools and not withheld:
            payload["tools"] = tools
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return f"{self.base_url}/api/chat", headers, payload
```

Extend `_request_for`:

```python
    def _request_for(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        if self.provider == "anthropic":
            return self._anthropic_request(messages, tools, tool_choice, requested)
        if self.provider == "ollama":
            return self._ollama_request(messages, tools, tool_choice, requested)
        return self._openai_request(messages, tools, tool_choice, requested)
```

In `stream`, select the normalizer:

```python
            events = (
                normalize_ndjson(response.iter_lines()) if self.provider == "ollama"
                else normalize_sse(response.iter_lines(), provider=self.provider)
            )
            for item in events:
                yield replace(item, sequence=item.sequence + (1 if has_limit else 0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic -v`

Expected: PASS — the seven new tests plus every pre-existing agentic test.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/provider_stream.py tests/unit/agentic/test_provider_stream_payload.py
git commit -m "feat(agentic): stream a turn from Ollama's native chat API"
```

---

## Task 9: Wire the worker to Ollama

**Files:**
- Modify: `src/agentos/workers/chat.py:20`, `:39-44`, `:192-230`
- Test: `tests/unit/workers/test_chat.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/workers/test_chat.py`. It reuses `_catalog_engine(*rows)` and the `Store` subclass pattern that the existing `_max_context_tokens_for` tests already use (around line 146):

```python
def _ollama_worker(*rows: dict[str, object]) -> ChatWorker:
    engine = _catalog_engine(*rows)

    class OllamaStore(Store):
        _engine = engine

    return ChatWorker(OllamaStore())


def test_num_ctx_never_exceeds_the_models_own_window() -> None:
    """A 262k model must not be asked to allocate a 262k KV cache."""
    worker = _ollama_worker({
        "user_id": "user-1", "provider": "ollama", "model_id": "qwen3:8b",
        "display_name": "qwen3:8b", "context_window": 262_144,
    })
    turn = {**TURN, "provider": "ollama", "model_id": "qwen3:8b"}

    budget = worker._max_context_tokens_for(turn) + chat_module.CONTEXT_WINDOW_RESERVE_TOKENS

    assert worker._num_ctx_for(turn) == budget
    assert budget < 262_144


def test_num_ctx_is_capped_by_a_small_models_window() -> None:
    worker = _ollama_worker({
        "user_id": "user-1", "provider": "ollama", "model_id": "tiny:1b",
        "display_name": "tiny:1b", "context_window": 8_192,
    })
    turn = {**TURN, "provider": "ollama", "model_id": "tiny:1b"}

    assert worker._num_ctx_for(turn) == 8_192


def test_num_ctx_falls_back_conservatively_for_an_uncatalogued_model() -> None:
    """The worker's 60k default would be a VRAM trap for an unknown model."""
    worker = _ollama_worker()
    turn = {**TURN, "provider": "ollama", "model_id": "ghost:1b"}

    assert worker._num_ctx_for(turn) == chat_module.OLLAMA_FALLBACK_NUM_CTX
    assert chat_module.OLLAMA_FALLBACK_NUM_CTX < chat_module.DEFAULT_MAX_CONTEXT_TOKENS


def test_base_url_resolution_covers_local_and_cloud_ollama() -> None:
    worker = _ollama_worker()

    assert worker._base_url_for("ollama", {"base_url": None}) == "http://localhost:11434"
    assert worker._base_url_for("ollama", {"base_url": "https://ollama.com/v1"}) == "https://ollama.com"
    assert worker._base_url_for("openai", {"base_url": None}) == chat_module.PROVIDER_BASE_URLS["openai"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/workers/test_chat.py -k "num_ctx or base_url_resolution" -v`

Expected: FAIL — `AttributeError: 'ChatWorker' object has no attribute '_num_ctx_for'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/workers/chat.py`, add the imports next to the OmniRoute one (line 20):

```python
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, normalize_ollama_base_url
```

Extend the base URL map and add the fallback constant after `CONTEXT_WINDOW_RESERVE_TOKENS`:

```python
PROVIDER_BASE_URLS = {
    "anthropic": "https://api.anthropic.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "omniroute": DEFAULT_OMNIROUTE_BASE_URL,
    "ollama": DEFAULT_OLLAMA_BASE_URL,
}

# num_ctx for an Ollama model the catalog has no window for. Deliberately not
# DEFAULT_MAX_CONTEXT_TOKENS: asking an unknown local model for a 60k KV cache
# is what spills into system RAM and drops inference by 20-50x.
OLLAMA_FALLBACK_NUM_CTX = 16_384
```

Split the catalog lookup out of `_max_context_tokens_for` and add the two new helpers. Replace `_max_context_tokens_for` with:

```python
    def _context_window_for(self, turn: dict[str, object]) -> int | None:
        """The turn model's real context window, or None if it is unknown.

        Best-effort only: any failure here (catalog not yet refreshed for
        this model, or -- in unit tests -- a store whose engine is a bare
        stub) reports None rather than blocking turn construction over a
        sizing refinement.
        """
        try:
            with self.store._engine.connect() as c:
                context_window = c.execute(
                    select(provider_model_catalog.c.context_window).where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == turn["provider"],
                        provider_model_catalog.c.model_id == turn["model_id"],
                    )
                ).scalar()
        except Exception:
            return None
        return int(context_window) if context_window else None

    def _max_context_tokens_for(self, turn: dict[str, object]) -> int:
        """Derive the context-trim budget from the turn's actual model window.

        The provider catalog already knows each model's real context window
        (used today only to filter model *selection*); this is the first
        place that spends it on the agentic loop's own request-sizing budget,
        instead of the flat 60k every model used to get regardless of what it
        can actually accept. A small or unrefreshed-catalog model falls back
        to a safe smaller budget rather than risking an oversized request.
        """
        context_window = self._context_window_for(turn)
        if context_window is None:
            return DEFAULT_MAX_CONTEXT_TOKENS
        return max(MIN_MAX_CONTEXT_TOKENS, min(DEFAULT_MAX_CONTEXT_TOKENS, context_window - CONTEXT_WINDOW_RESERVE_TOKENS))

    def _num_ctx_for(self, turn: dict[str, object]) -> int:
        """The KV cache Ollama should allocate for this turn.

        Never more than the window this turn will actually fill, and never
        more than the model can hold: both ceilings matter, because the cache
        is real VRAM on the user's own machine.
        """
        context_window = self._context_window_for(turn)
        if context_window is None:
            return OLLAMA_FALLBACK_NUM_CTX
        return min(context_window, self._max_context_tokens_for(turn) + CONTEXT_WINDOW_RESERVE_TOKENS)

    def _base_url_for(self, provider: str, credential) -> str:
        configured = str(credential.get("base_url") or "")
        if provider == "omniroute":
            return normalize_omniroute_base_url(configured or DEFAULT_OMNIROUTE_BASE_URL)
        if provider == "ollama":
            return normalize_ollama_base_url(configured or DEFAULT_OLLAMA_BASE_URL)
        return PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS["openrouter"])
```

Replace the body of `_provider_transport` after the credential lookup:

```python
        provider, model = str(turn["provider"]), str(turn["model_id"])
        api_key = self._credential_value(credential, str(turn["user_id"]), provider, allow_empty=provider in PROVIDERS_WITH_BASE_URL)
        base_url = self._base_url_for(provider, credential)
        num_ctx = self._num_ctx_for(turn) if provider == "ollama" else None
        return HTTPProviderStreamTransport(provider=provider, base_url=base_url, api_key=api_key, model=model, num_ctx=num_ctx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/workers/test_chat.py -v`

Expected: PASS — the four new tests plus the four pre-existing `_max_context_tokens_for` tests, unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/workers/chat.py tests/unit/workers/test_chat.py
git commit -m "feat(workers): run a turn against a local or hosted Ollama"
```

---

## Task 10: Persist an Ollama credential with a Cloud-aware key rule

**Files:**
- Modify: `src/agentos/persistence/postgres/provider_configuration.py:47-49`, `:52-59`, `:74-75`, `:135-144`, `:160-170`
- Test: `tests/unit/persistence/test_provider_configuration.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create or append to `tests/unit/persistence/test_provider_configuration.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher


def _adapter() -> PostgresProviderConfigurationAdapter:
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_configurations])
    return PostgresProviderConfigurationAdapter(engine, cipher=ProviderSecretCipher(b"0" * 32))


def _command(**overrides: object) -> dict[str, object]:
    return {"provider": "ollama", "user_id": "user-1", "enabled": True, "api_key": "", "base_url": None, **overrides}


def test_a_local_ollama_is_configured_without_a_key() -> None:
    state = _adapter().configure(_command(base_url="http://localhost:11434"))

    assert state["provider"] == "ollama"
    assert state["enabled"] is True
    assert state["base_url"] == "http://localhost:11434"
    assert not any("api_key" in key for key in state)


def test_the_local_default_applies_when_no_url_is_given() -> None:
    assert _adapter().configure(_command())["base_url"] == "http://localhost:11434"


def test_ollama_cloud_refuses_to_be_configured_without_a_key() -> None:
    """The mode comes from the host, so the key rule has to read it too."""
    with pytest.raises(ValueError):
        _adapter().configure(_command(base_url="https://ollama.com"))


def test_ollama_cloud_is_configured_with_a_key() -> None:
    state = _adapter().configure(_command(base_url="https://ollama.com/v1", api_key="cloud-secret"))

    assert state["base_url"] == "https://ollama.com"


def test_a_rejected_provider_still_cannot_be_connection_tested() -> None:
    with pytest.raises(ValueError):
        _adapter().test_connection({"provider": "openai", "api_key": "k", "base_url": None})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/persistence/test_provider_configuration.py -v`

Expected: FAIL — `ValueError: provider API key is required`, because `_api_key` still requires four characters for every provider but `omniroute`.

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/persistence/postgres/provider_configuration.py`, add the imports next to the OmniRoute ones:

```python
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL, PROVIDERS_WITH_OPTIONAL_KEY
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, OllamaCatalogClient, is_ollama_cloud, normalize_ollama_base_url
```

Replace the trailing expression of `_public`:

```python
        **({"base_url": row["base_url"]} if str(row["provider"]) in PROVIDERS_WITH_BASE_URL and row.get("base_url") else {}),
```

In `configure`, swap the two lines so the URL is known before the key is judged:

```python
        base_url = _base_url(provider, command.get("base_url"))
        api_key = _api_key(provider, command.get("api_key"), base_url)
```

Replace both `allow_empty=provider == "omniroute"` occurrences in `configure` with:

```python
allow_empty=provider in PROVIDERS_WITH_OPTIONAL_KEY
```

Replace `_base_url` and `_api_key` at the bottom of the module:

```python
def _base_url(provider: str, value: object) -> str | None:
    if provider == "omniroute":
        return normalize_omniroute_base_url(str(value or DEFAULT_OMNIROUTE_BASE_URL))
    if provider == "ollama":
        return normalize_ollama_base_url(str(value or DEFAULT_OLLAMA_BASE_URL))
    return None


def _api_key(provider: str, value: object, base_url: str | None = None) -> str:
    api_key = str(value or "")
    optional = provider in PROVIDERS_WITH_OPTIONAL_KEY
    # Local Ollama needs no credential; the hosted service always does. The
    # host is the only thing that distinguishes the two, so it decides here.
    if provider == "ollama" and base_url is not None and is_ollama_cloud(base_url):
        optional = False
    if not optional and len(api_key) < 4:
        raise ValueError("provider API key is required")
    return api_key
```

Replace `test_connection` so it dispatches instead of rejecting everything but OmniRoute:

```python
    def test_connection(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command.get("provider"))
        if provider not in PROVIDERS_WITH_BASE_URL:
            raise ValueError("connection testing is not available for this provider")
        base_url = _base_url(provider, command.get("base_url"))
        client = OmniRouteCatalogClient() if provider == "omniroute" else OllamaCatalogClient()
        try:
            models = client.fetch(str(command["api_key"]), base_url=str(base_url))
        except RuntimeError as error:
            # The adapter only permits the gateway's safe generic response.
            raise ValueError("provider connection failed") from error
        return {"connected": True, "models_available": len(models), "base_url": base_url}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/persistence -v`

Expected: PASS for the five new tests. The two pre-existing failures noted in the plan header (`test_postgres_schema.py::test_persistence_schema_contains_only_the_durable_boundary_tables` and `test_postgres_adapter.py::test_migrations_can_downgrade_to_initial_revision`) are unrelated and remain failing — do not fix them here.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/provider_configuration.py tests/unit/persistence/test_provider_configuration.py
git commit -m "feat(persistence): store an Ollama credential for local or cloud"
```

---

## Task 11: Expose Ollama through the gateway

**Files:**
- Modify: `src/agentos/api/gateway.py:702-704`, `:736-746`, `:934-938`, `:962-971`
- Test: `tests/unit/api/test_api_asgi.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/api/test_api_asgi.py`, following the existing client/fake fixtures in that file:

```python
def test_ollama_is_an_accepted_provider_name() -> None:
    from agentos.api.gateway import _provider_name

    assert _provider_name("Ollama") == "ollama"
    with pytest.raises(ValueError):
        _provider_name("llamafile")


def test_configuring_ollama_does_not_require_a_key_at_the_gateway() -> None:
    """Local Ollama is keyless; the Cloud rule lives in the adapter, which
    is the only layer that sees the base URL."""
    import inspect

    from agentos.api import gateway

    source = inspect.getsource(gateway)
    assert 'provider_name not in PROVIDERS_WITH_OPTIONAL_KEY and payload.api_key is None' in source
    assert '@app.post("/v1/providers/ollama/test")' in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_api_asgi.py -k ollama -v`

Expected: FAIL — `ValueError: unsupported provider` for `_provider_name("Ollama")`

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/api/gateway.py`, extend the existing `provider_catalog.models` import to bring in the shared rule (the gateway already imports `ProviderCatalogContext` from there, so this adds no new dependency):

```python
from agentos.provider_catalog.models import PROVIDERS_WITH_OPTIONAL_KEY, ProviderCatalogContext
```

Extend `_provider_name`:

```python
def _provider_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"openai", "anthropic", "openrouter", "omniroute", "ollama"}:
        raise ValueError("unsupported provider")
    return normalized
```

Replace the key guard in `configure_provider` (line 703):

```python
        if provider_name not in PROVIDERS_WITH_OPTIONAL_KEY and payload.api_key is None:
            raise ValueError("API key is required for this provider")
```

Rename `_omniroute_test_public` to `_connection_test_public`, generalize its message, and update the existing OmniRoute route to call it:

```python
def _connection_test_public(value: object) -> dict[str, object]:
    data = _jsonable(value)
    if not isinstance(data, dict) or data.get("connected") is not True:
        raise ValueError("provider connection response is invalid")
    count = data.get("models_available")
    return {
        "connected": True,
        "models_available": count if isinstance(count, int) and count >= 0 else None,
        "base_url": data.get("base_url") if isinstance(data.get("base_url"), str) else None,
    }
```

Add the new route immediately after `test_omniroute_connection`:

```python
    @app.post("/v1/providers/ollama/test")
    async def test_ollama_connection(payload: ProviderSetupRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.test", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.test", resource_id="ollama", purpose=payload.purpose)
        _idempotency(request)
        # Reaching a local daemon is blocking I/O; keeping it off the event
        # loop matters more here than for a hosted gateway, because an Ollama
        # that is simply not running takes the full connect timeout to fail.
        result = await run_in_threadpool(
            _require_port(services.provider_configuration).test_connection,
            {"provider": "ollama", "user_id": principal.user_id, "purpose": payload.purpose,
             "api_key": payload.api_key.get_secret_value() if payload.api_key is not None else "",
             "base_url": payload.base_url},
        )
        return JSONResponse(_connection_test_public(result))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api -v`

Expected: PASS — the two new tests plus every pre-existing API test.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/gateway.py tests/unit/api/test_api_asgi.py
git commit -m "feat(api): configure and connection-test the Ollama provider"
```

---

## Task 12: Make Ollama visible to the model resolver

**Files:**
- Modify: `src/agentos/provider_catalog/resolver_catalog.py:69`
- Test: `tests/unit/provider_catalog/test_resolver_adapter.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/provider_catalog/test_resolver_adapter.py`:

```python
def test_the_resolver_catalog_covers_every_configurable_provider() -> None:
    """A provider missing from this tuple is invisible to model resolution.
    OmniRoute was absent since it shipped; Ollama must not repeat that."""
    import inspect

    from agentos.provider_catalog import resolver_catalog

    listing = inspect.getsource(resolver_catalog.PostgresProviderModelCatalog.list_models)
    for provider in ("openrouter", "openai", "anthropic", "omniroute", "ollama"):
        assert f'"{provider}"' in listing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog/test_resolver_adapter.py -k every_configurable -v`

Expected: FAIL — `AssertionError` on `"omniroute"`

- [ ] **Step 3: Write minimal implementation**

In `src/agentos/provider_catalog/resolver_catalog.py`, replace line 69:

```python
        for provider in ("openrouter", "openai", "anthropic", "omniroute", "ollama"):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/provider_catalog -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/resolver_catalog.py tests/unit/provider_catalog/test_resolver_adapter.py
git commit -m "fix(provider-catalog): resolve models from every configurable provider"
```

---

## Task 13: Frontend provider client

**Files:**
- Modify: `frontend/src/api/providers.ts:4`, and after `testOmniRouteConnection` (line 125)
- Test: `frontend/tests/unit/ProviderSettingsPage.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/tests/unit/ProviderSettingsPage.test.tsx`:

```tsx
import { PROVIDER_NAMES, testOllamaConnection } from '../../src/api/providers'

describe('ollama provider client', () => {
  it('is offered alongside the other providers', () => {
    expect(PROVIDER_NAMES).toContain('ollama')
  })

  it('posts the base url and key to the ollama test route', async () => {
    const seen: Array<Record<string, unknown>> = []
    const client = {
      createMutationIntent: () => ({ key: 'intent-1' }),
      request: async (options: Record<string, unknown>) => {
        seen.push(options)
        return (options.parse as (value: unknown) => unknown)({ connected: true, models_available: 3, base_url: 'http://localhost:11434' })
      },
    } as never

    const result = await testOllamaConnection(client, { apiKey: '', baseUrl: 'http://localhost:11434' })

    expect(seen[0].path).toBe('/v1/providers/ollama/test')
    expect(seen[0].method).toBe('POST')
    expect(seen[0].body).toEqual({ api_key: '', base_url: 'http://localhost:11434' })
    expect(result).toEqual({ connected: true, models_available: 3, base_url: 'http://localhost:11434' })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/ProviderSettingsPage.test.tsx`

Expected: FAIL — `testOllamaConnection` is not exported.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/api/providers.ts`, extend the provider list:

```ts
export const PROVIDER_NAMES = ['openai', 'anthropic', 'openrouter', 'omniroute', 'ollama'] as const
```

Add after `testOmniRouteConnection` (the response shape is identical, so it reuses `OmniRouteConnection`):

```ts
export function testOllamaConnection(
  client: ApiClient,
  input: { apiKey: string; baseUrl: string },
  intent = client.createMutationIntent(),
): Promise<OmniRouteConnection> {
  return client.request({
    path: `${providerPath('ollama')}/test`, method: 'POST', intent,
    body: { api_key: input.apiKey, base_url: input.baseUrl },
    parse: (value) => {
      const data = record(value)
      if (data.connected !== true) throw invalidResponseError()
      return { connected: true, models_available: data.models_available === null ? null : nonNegativeInteger(data.models_available), base_url: nullableString(data.base_url) }
    },
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/ProviderSettingsPage.test.tsx`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/providers.ts frontend/tests/unit/ProviderSettingsPage.test.tsx
git commit -m "feat(web): call the Ollama provider routes"
```

---

## Task 14: The Ollama settings panel

**Files:**
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx:5`, `:57-72`, `:232-262`, `:338-349`
- Test: `frontend/tests/unit/ProviderSettingsPage.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/tests/unit/ProviderSettingsPage.test.tsx`. It reuses the `client`, `providerFetch` and `json` helpers already defined at the bottom of that file, and finds the panel the same way `openRouterPanel` does:

```tsx
async function ollamaPanel(): Promise<HTMLElement> {
  return screen.findByRole('article', { name: 'Ollama' })
}

describe('ProviderSettingsPage Ollama setup', () => {
  it('starts in local mode with the default url and no key field', async () => {
    render(<ProviderSettingsPage client={client(providerFetch(() => json({ provider: 'ollama', enabled: true })))} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await ollamaPanel()

    expect(within(panel).getByLabelText('URL do servidor')).toHaveValue('http://localhost:11434')
    expect(within(panel).queryByLabelText('Chave de API')).toBeNull()
  })

  it('switches to cloud, swaps the url and asks for a key', async () => {
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(providerFetch(() => json({ provider: 'ollama', enabled: true })))} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await ollamaPanel()
    await user.click(within(panel).getByRole('radio', { name: 'Cloud' }))

    expect(within(panel).getByLabelText('URL do servidor')).toHaveValue('https://ollama.com')
    expect(within(panel).getByLabelText('Chave de API')).toBeInTheDocument()
  })

  it('will not save a cloud connection without a key', async () => {
    const user = userEvent.setup()
    render(<ProviderSettingsPage client={client(providerFetch(() => json({ provider: 'ollama', enabled: true })))} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />)

    const panel = await ollamaPanel()
    await user.click(within(panel).getByRole('radio', { name: 'Cloud' }))

    expect(within(panel).getByRole('button', { name: 'Salvar e ativar' })).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/ProviderSettingsPage.test.tsx`

Expected: FAIL — no element labelled `Ollama` is rendered.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/features/providers/ProviderSettingsPage.tsx`, add `testOllamaConnection` to the import list on line 5, then add the `ollama` case to `providerLabel`:

```tsx
    case 'ollama':
      return 'Ollama'
```

Add the mode state inside `ProviderPanel`, next to the existing `baseUrl` state:

```tsx
  // Ollama's mode is not stored: it is read back from the saved URL's host,
  // exactly as the backend derives it. One source, so the two cannot disagree.
  const [ollamaMode, setOllamaMode] = useState<'local' | 'cloud'>('local')
```

In the existing `inspectProvider` effect, restore the mode from the saved URL by adding, next to the OmniRoute base URL restore:

```tsx
        if (provider === 'ollama' && typeof state.extra.base_url === 'string') {
          setBaseUrl(state.extra.base_url)
          setOllamaMode(/(^|\.)ollama\.com$/i.test(new URL(state.extra.base_url).hostname) ? 'cloud' : 'local')
        }
```

Add an `onTestOllama` handler beside `onTestConnection`:

```tsx
  async function onTestOllama() {
    if (provider !== 'ollama' || action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'test' })
    try {
      const result = await testOllamaConnection(client, { apiKey, baseUrl }, intentFor('test'))
      intents.current.delete('test')
      setConnection({ connected: true, models: result.models_available })
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setConnection(null)
      setAction({ pending: false, error: toApiError(error), kind: 'test' })
    }
  }
```

Define `isOllama` next to `isOmniRoute`, render the panel, and exclude Ollama from the generic form by changing its condition from `{!isOmniRoute && ...}` to `{!isOmniRoute && !isOllama && ...}`:

```tsx
  const isOllama = provider === 'ollama'
```

```tsx
      {isOllama && <OllamaSetup
        mode={ollamaMode}
        enabled={enabled}
        apiKey={apiKey}
        baseUrl={baseUrl}
        action={action}
        canRevoke={canRevoke}
        bootstrap={bootstrap}
        connection={connection}
        onModeChange={(mode) => { setOllamaMode(mode); setBaseUrl(mode === 'cloud' ? 'https://ollama.com' : 'http://localhost:11434'); setConnection(null) }}
        onTest={onTestOllama}
        onSave={onSave}
        onRevoke={onRevoke}
        onApiKeyChange={setApiKey}
        onBaseUrlChange={setBaseUrl}
        onEnabledChange={setEnabled}
      />}
```

Add the component after `OmniRouteFreeSetup`:

```tsx
type OllamaSetupProps = {
  mode: 'local' | 'cloud'
  enabled: boolean
  apiKey: string
  baseUrl: string
  action: ActionState
  canRevoke: boolean
  bootstrap: BrowserSessionBootstrap
  connection: { connected: boolean; models: number | null } | null
  onModeChange: (mode: 'local' | 'cloud') => void
  onTest: () => void
  onSave: (event: FormEvent<HTMLFormElement>) => void
  onRevoke: () => void
  onApiKeyChange: (value: string) => void
  onBaseUrlChange: (value: string) => void
  onEnabledChange: (value: boolean) => void
}

/**
 * Local and Cloud are one provider row, so this is a mode switch rather than
 * two panels: the backend derives the mode from the saved host, and the only
 * difference the user has to care about is that Cloud needs a key.
 */
function OllamaSetup(props: OllamaSetupProps) {
  const sessionUnavailable = props.bootstrap.status === 'missing_csrf'
  const isTesting = props.action.pending && props.action.kind === 'test'
  const isSaving = props.action.pending && props.action.kind === 'configure'
  const isCloud = props.mode === 'cloud'

  return <form className="ollama-flow" onSubmit={props.onSave}>
    <div className="ollama-flow__modes" role="radiogroup" aria-label="Modo do Ollama">
      {(['local', 'cloud'] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          role="radio"
          aria-checked={props.mode === mode}
          className={props.mode === mode ? 'chip is-selected' : 'chip'}
          disabled={props.action.pending || sessionUnavailable}
          onClick={() => props.onModeChange(mode)}
        >
          {mode === 'local' ? 'Local' : 'Cloud'}
        </button>
      ))}
    </div>
    <p>{isCloud
      ? 'Modelos hospedados pela Ollama. Precisa de uma chave criada em ollama.com/settings/keys.'
      : 'Modelos rodando nesta máquina (ou em outra da sua rede). Nenhuma chave é necessária.'}</p>

    <div className="ollama-flow__fields">
      <label htmlFor="ollama-base-url">URL do servidor</label>
      <input id="ollama-base-url" name="base-url" type="url" autoComplete="off" value={props.baseUrl} onChange={(event) => props.onBaseUrlChange(event.target.value)} />
      {isCloud && <>
        <label htmlFor="ollama-api-key">Chave de API</label>
        <input id="ollama-api-key" name="api-key" type="password" autoComplete="off" value={props.apiKey} onChange={(event) => props.onApiKeyChange(event.target.value)} placeholder="Inserir uma nova chave" />
      </>}
    </div>

    <label className="provider-panel__toggle" htmlFor="ollama-enabled">
      <input id="ollama-enabled" type="checkbox" checked={props.enabled} onChange={(event) => props.onEnabledChange(event.target.checked)} />
      Ativar Ollama para os agentes
    </label>

    <div className="ollama-flow__actions">
      <button type="button" className="button button--secondary" disabled={props.action.pending || sessionUnavailable} onClick={props.onTest}>
        {isTesting ? 'Testando conexão…' : 'Testar conexão'}
      </button>
      <button type="submit" className="button button--primary" disabled={props.action.pending || sessionUnavailable || (isCloud && !props.apiKey)}>
        {isSaving ? 'Salvando…' : 'Salvar e ativar'}
      </button>
      <button type="button" className="button button--secondary button--danger" disabled={props.action.pending || !props.canRevoke || sessionUnavailable} onClick={props.onRevoke}>
        Desativar acesso
      </button>
    </div>

    {props.connection?.connected && <p className="omniroute-step__success" aria-live="polite">
      Conexão pronta{props.connection.models === null ? '' : ` · ${props.connection.models} modelos disponíveis`}.
    </p>}
    {sessionUnavailable && <p role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de continuar.</p>}
    {props.action.error && <ProviderErrorNotice error={props.action.error} action={props.action.kind} />}
  </form>
}
```

Finally, extend the auto-refresh in `onSave` so saving Ollama fills the catalog the same way it does for OmniRoute — change its condition from `provider === 'omniroute'` to:

```tsx
      if ((provider === 'omniroute' || provider === 'ollama') && state.enabled === true) {
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/ProviderSettingsPage.test.tsx`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/providers/ProviderSettingsPage.tsx frontend/tests/unit/ProviderSettingsPage.test.tsx
git commit -m "feat(web): configure Ollama in local or cloud mode"
```

---

## Task 15: Mark models that cannot drive the loop

**Files:**
- Modify: `frontend/src/components/ModelPicker.tsx:45-51`, `:120-127`
- Test: `frontend/tests/unit/ModelPicker.test.tsx` (create if absent)

- [ ] **Step 1: Write the failing test**

Create or append to `frontend/tests/unit/ModelPicker.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ModelPicker } from '../../src/components/ModelPicker'
import type { ProviderModel } from '../../src/api/providers'

function model(overrides: Partial<ProviderModel>): ProviderModel {
  return {
    provider: 'ollama', model_id: 'm', display_name: 'M', context_window: 8192,
    capabilities: ['completion', 'tools'], input_modalities: ['text'], output_modalities: ['text'],
    pricing: null, is_favorite: false, refreshed_at: null, route_kind: 'model', ...overrides,
  }
}

function picker(models: ProviderModel[]) {
  return <ModelPicker
    providers={['ollama']} provider="ollama" onProviderChange={vi.fn()}
    models={models} modelId="" onModelChange={vi.fn()}
  />
}

describe('ModelPicker tool support', () => {
  it('marks a model that cannot use tools', async () => {
    render(picker([model({ model_id: 'plain:1b', display_name: 'Plain', capabilities: ['completion'] })]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getByRole('option', { name: /Plain/ })).toHaveTextContent('sem ferramentas')
  })

  it('does not mark a model that supports tools', async () => {
    render(picker([model({ model_id: 'qwen3:8b', display_name: 'Qwen3' })]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getByRole('option', { name: /Qwen3/ })).not.toHaveTextContent('sem ferramentas')
  })

  it('ranks tool-capable models above the rest', async () => {
    render(picker([
      model({ model_id: 'plain:1b', display_name: 'Plain', capabilities: ['completion'] }),
      model({ model_id: 'qwen3:8b', display_name: 'Qwen3' }),
    ]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getAllByRole('option').map((item) => item.textContent)).toEqual([
      expect.stringContaining('Qwen3'),
      expect.stringContaining('Plain'),
    ])
  })

  it('leaves a provider that reports no capabilities unmarked', async () => {
    render(picker([model({ provider: 'openai', model_id: 'gpt', display_name: 'GPT', capabilities: [] })]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getByRole('option', { name: /GPT/ })).not.toHaveTextContent('sem ferramentas')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/ModelPicker.test.tsx`

Expected: FAIL — the option has no `sem ferramentas` text.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/ModelPicker.tsx`, add the helper above the component:

```tsx
/**
 * Orin's loop is tool-driven, so a model that cannot call tools cannot run a
 * turn. Ollama is the first provider to report this per model; a provider
 * that reports no capabilities at all says nothing either way, and its
 * models are left unmarked rather than wrongly accused.
 */
function lacksToolSupport(model: ProviderModel): boolean {
  return model.capabilities.length > 0 && !model.capabilities.includes('tools')
}
```

Change the ranking in the `filtered` memo so unusable models sink:

```tsx
    const ranked = [...props.models].sort((a, b) =>
      Number(lacksToolSupport(a)) - Number(lacksToolSupport(b)) || Number(b.is_favorite) - Number(a.is_favorite))
```

Add the marker inside the model option button, after the context-window span:

```tsx
                    {lacksToolSupport(model) ? <span className="picker-option__meta picker-option__meta--warn" title="Este modelo não chama ferramentas; o Orin não consegue executar um turno com ele.">sem ferramentas</span> : null}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/ModelPicker.test.tsx`

Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModelPicker.tsx frontend/tests/unit/ModelPicker.test.tsx
git commit -m "feat(web): flag models that cannot call tools"
```

---

## Task 16: End-to-end tool loop over an NDJSON stream

**Files:**
- Modify: `tests/integration/agentic/test_provider_tool_loop.py`

- [ ] **Step 1: Read the existing integration test to reuse its harness**

Run: `.venv/Scripts/python.exe -m pytest tests/integration/agentic/test_provider_tool_loop.py -v`

Expected: PASS. Read the file and note how it builds its fake transport and asserts the loop's tool round trip — the new test mirrors that structure with NDJSON instead of SSE.

- [ ] **Step 2: Write the failing test**

Append to `tests/integration/agentic/test_provider_tool_loop.py`. The test is self-contained — it imports everything it needs and defines its own transport, so no fixture from the surrounding file has to be adapted:

```python
def test_ollama_ndjson_stream_drives_a_full_tool_round_trip() -> None:
    """The native stream must reach the runtime as the same typed events an
    SSE provider produces, tool call included."""
    import json

    import httpx

    from agentos.agentic.provider_stream import HTTPProviderStreamTransport, StreamKind

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            body = "\n".join([
                json.dumps({"message": {"role": "assistant", "content": "checking"}, "done": False}),
                json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]}, "done": False}),
                json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 40, "eval_count": 9}),
            ])
        else:
            body = "\n".join([
                json.dumps({"message": {"role": "assistant", "content": "done"}, "done": False}),
                json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 60, "eval_count": 3}),
            ])
        return httpx.Response(200, text=body)

    transport = HTTPProviderStreamTransport(
        provider="ollama", base_url="http://localhost:11434", api_key="", model="qwen3:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)), num_ctx=32_768,
    )
    request = {
        "messages": [{"role": "user", "content": "read a.txt"}],
        "tools": [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {}}}}],
    }

    first = list(transport.stream(request))
    second = list(transport.stream({**request, "tool_choice": "none"}))

    assert [item.kind for item in first] == [StreamKind.TEXT, StreamKind.TOOL_CALL, StreamKind.USAGE, StreamKind.FINISH]
    assert first[1].tool_call_id == "tool-call:1"
    assert json.loads(first[1].arguments_delta) == {"path": "a.txt"}
    assert first[3].finish_reason.value == "TOOL_CALLS"
    assert first[2].usage.total_tokens == 49

    assert calls[0]["options"]["num_ctx"] == 32_768
    assert "tools" in calls[0]
    # The closing iteration withholds the declarations entirely.
    assert "tools" not in calls[1]
    assert [item.kind for item in second] == [StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert second[2].finish_reason.value == "STOP"
```

- [ ] **Step 3: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/integration/agentic/test_provider_tool_loop.py -v`

Expected: PASS — this test exercises code delivered in Tasks 7 and 8, so it should pass on the first run. If it fails, the defect is in `normalize_ndjson` or `_ollama_request`, not in the test.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q`

Expected: PASS, except the two pre-existing failures named in the plan header.

Run: `cd frontend && npx vitest run && npx tsc -b && npx eslint . --max-warnings=0`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/agentic/test_provider_tool_loop.py
git commit -m "test(agentic): cover a full Ollama tool round trip"
```

---

## Out of Scope

Deliberately excluded, per the spec:

- An installer or runtime manager for Ollama. OmniRoute has one because it is an npm package; Ollama ships a native app installer, so the equivalent here is a link, not a command.
- The reasoning channel (`think` / `message.thinking`). It would need a new event kind on `NormalizedStreamItem` plus runtime and UI handling.
- Embeddings (`/api/embed`) and model management (`/api/pull`, `/api/delete`).
- Per-token pricing for Cloud. The API publishes none, so `pricing` stays `None`, as it is for OmniRoute.
