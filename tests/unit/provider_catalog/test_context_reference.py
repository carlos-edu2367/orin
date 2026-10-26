from __future__ import annotations

from datetime import UTC, datetime

import httpx

from agentos.provider_catalog.context_reference import (
    OpenRouterContextReference,
    ReferenceContextWindows,
    context_window_from_id,
)
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.nvidia import NvidiaModelCatalogClient
from agentos.provider_catalog.service import ProviderModelCatalogService

from .test_nvidia import _FakeNvidiaRepo


def test_reference_matches_the_same_slug_under_a_different_vendor_prefix() -> None:
    lookup = ReferenceContextWindows([
        {"id": "minimax/minimax-m3.1-flash", "context_length": 1048576},
        {"id": "deepseek/deepseek-v4.1-flash", "context_length": 1048576},
        {"id": "nvidia/nemotron-3-embed-1b:free", "context_length": 32768},
        {"id": "speech/model", "context_length": 0},
    ])

    assert lookup("minimaxai/minimax-m3.1-flash") == 1048576
    assert lookup("deepseek-ai/deepseek-v4.1-flash") == 1048576
    assert lookup("nvidia/nemotron-3-embed-1b") == 32768
    assert lookup("speech/model") is None
    assert lookup("unknown/never-seen") is None


def test_reference_prefers_the_exact_id_and_otherwise_the_smaller_window() -> None:
    lookup = ReferenceContextWindows([
        {"id": "vendor-a/shared-model", "context_length": 200000},
        {"id": "vendor-b/shared-model", "context_length": 32000},
    ])

    assert lookup("vendor-a/shared-model") == 200000
    assert lookup("vendor-c/shared-model") == 32000


def test_window_spelled_into_the_id_is_a_fallback() -> None:
    assert context_window_from_id("microsoft/phi-3-vision-128k-instruct") == 128000
    assert context_window_from_id("writer/palmyra-fin-70b-32k") == 32000
    assert context_window_from_id("meta/llama-3.1-70b-instruct") is None
    assert ReferenceContextWindows([])("nvidia/mistral-nemo-minitron-8b-8k-instruct") == 8000


def test_openrouter_reference_reads_the_public_catalog_without_a_key(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def get(url: str, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return httpx.Response(200, json={"data": [{"id": "z-ai/glm-5.3", "context_length": 1048576}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("agentos.provider_catalog.context_reference.httpx.get", get)

    lookup = OpenRouterContextReference().load()
    assert lookup("z-ai/glm-5.3") == 1048576
    assert seen == {"url": "https://openrouter.ai/api/v1/models", "headers": None}


class _StaticReference:
    def __init__(self, windows: dict[str, int] | None = None, *, fail: bool = False) -> None:
        self.windows = windows or {}
        self.fail = fail
        self.loads = 0

    def load(self):
        self.loads += 1
        if self.fail:
            raise httpx.ConnectError("offline")
        return ReferenceContextWindows([{"id": key, "context_length": value} for key, value in self.windows.items()])


def _nvidia_service(payload: dict[str, object], reference: _StaticReference) -> tuple[ProviderModelCatalogService, _FakeNvidiaRepo]:
    repo = _FakeNvidiaRepo({"enabled": True, "api_key": "nvapi-test", "base_url": "https://integrate.api.nvidia.com/v1"})
    client = NvidiaModelCatalogClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    service = ProviderModelCatalogService(
        repo, {}, nvidia_client=client, context_reference=reference, now=lambda: datetime(2026, 10, 26, tzinfo=UTC),
    )
    return service, repo


CTX = ProviderCatalogContext(user_id="user-1", purpose="provider.catalog.refresh")


def test_nvidia_refresh_fills_missing_context_windows_from_the_reference() -> None:
    reference = _StaticReference({"minimax/minimax-m3.1-flash": 1048576})
    service, repo = _nvidia_service({"data": [
        {"id": "minimaxai/minimax-m3.1-flash", "object": "model", "owned_by": "minimaxai"},
        {"id": "meta/llama-3.1-70b-instruct", "max_model_len": 65536},
        {"id": "microsoft/phi-3-vision-128k-instruct"},
        {"id": "adept/fuyu-8b"},
    ]}, reference)

    service.refresh(CTX, "nvidia")

    windows = {record.model_id: record.context_window for record in repo.saved}
    assert windows == {
        "minimaxai/minimax-m3.1-flash": 1048576,
        "meta/llama-3.1-70b-instruct": 65536,
        "microsoft/phi-3-vision-128k-instruct": 128000,
        "adept/fuyu-8b": None,
    }
    assert reference.loads == 1


def test_nvidia_refresh_survives_an_unreachable_reference() -> None:
    reference = _StaticReference(fail=True)
    service, repo = _nvidia_service({"data": [
        {"id": "minimaxai/minimax-m3.1-flash"},
        {"id": "writer/palmyra-fin-70b-32k"},
    ]}, reference)

    receipt = service.refresh(CTX, "nvidia")

    assert receipt.count == 2
    assert {record.model_id: record.context_window for record in repo.saved} == {
        "minimaxai/minimax-m3.1-flash": None,
        "writer/palmyra-fin-70b-32k": 32000,
    }


def test_nvidia_refresh_skips_the_reference_when_every_window_is_known() -> None:
    reference = _StaticReference()
    service, _ = _nvidia_service({"data": [{"id": "meta/llama-3.1-70b-instruct", "max_model_len": 131072}]}, reference)

    service.refresh(CTX, "nvidia")

    assert reference.loads == 0
