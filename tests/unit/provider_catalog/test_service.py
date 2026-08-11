from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderCatalogUnavailable, ProviderModelCatalogService


class FakeRepository:
    def __init__(self) -> None:
        self.configured = {("user-a", "openrouter"): {"enabled": True, "api_key": "secret-for-user-a"}}
        self.records: dict[tuple[str, str], list[object]] = {}
        self.favorites: set[tuple[str, str, str]] = set()

    def credential(self, context: ProviderCatalogContext, provider: str) -> dict[str, object] | None:
        return self.configured.get((context.user_id, provider))

    def replace(self, context: ProviderCatalogContext, provider: str, records: list[object], refreshed_at: datetime) -> None:
        self.records[(context.user_id, provider)] = records

    def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False) -> list[object]:
        records = self.records.get((context.user_id, provider), [])
        items = [replace(record, is_favorite=(context.user_id, provider, record.model_id) in self.favorites) for record in records]
        return [item for item in items if item.is_favorite] if favorites_only else items

    def set_favorite(self, context: ProviderCatalogContext, provider: str, model_id: str, favorite: bool) -> object:
        known = [record for record in self.records.get((context.user_id, provider), []) if record.model_id == model_id]
        if not known:
            raise LookupError(model_id)
        key = (context.user_id, provider, model_id)
        if favorite:
            self.favorites.add(key)
        else:
            self.favorites.discard(key)
        return self.list(context, provider)[0]


class FakeOpenRouter:
    def __init__(self) -> None:
        self.fail = False

    def fetch(self, api_key: str) -> list[dict[str, object]]:
        assert api_key == "secret-for-user-a"
        if self.fail:
            raise TimeoutError("upstream was unavailable")
        return [{
            "id": "anthropic/claude-test",
            "name": "Claude Test",
            "description": "raw upstream text must not persist",
            "context_length": 200000,
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "supported_parameters": ["tools", "temperature"],
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        }]


def context(user_id: str) -> ProviderCatalogContext:
    return ProviderCatalogContext(user_id=user_id, purpose="provider.catalog.refresh")


def test_refresh_normalizes_openrouter_models_and_keeps_them_owner_scoped() -> None:
    repository = FakeRepository()
    upstream = FakeOpenRouter()
    service = ProviderModelCatalogService(repository, {"openrouter": upstream}, now=lambda: datetime(2026, 8, 10, tzinfo=UTC))

    receipt = service.refresh(context("user-a"), "openrouter")
    items = service.list(context("user-a"), "openrouter")

    assert receipt.count == 1
    assert items[0].provider == "openrouter"
    assert items[0].model_id == "anthropic/claude-test"
    assert items[0].display_name == "Claude Test"
    assert items[0].context_window == 200000
    assert items[0].input_modalities == ("text", "image")
    assert items[0].pricing.input_per_million == "3"
    assert "secret-for-user-a" not in repr(items[0])
    assert "raw upstream text" not in repr(items[0])
    assert service.list(context("user-b"), "openrouter") == []


def test_refresh_failure_keeps_existing_cache_readable() -> None:
    repository = FakeRepository()
    upstream = FakeOpenRouter()
    service = ProviderModelCatalogService(repository, {"openrouter": upstream}, now=lambda: datetime(2026, 8, 10, tzinfo=UTC))
    service.refresh(context("user-a"), "openrouter")
    upstream.fail = True

    with pytest.raises(ProviderCatalogUnavailable):
        service.refresh(context("user-a"), "openrouter")

    assert [item.model_id for item in service.list(context("user-a"), "openrouter")] == ["anthropic/claude-test"]


def test_favorites_are_scoped_to_catalog_identity_and_do_not_change_credentials() -> None:
    repository = FakeRepository()
    service = ProviderModelCatalogService(repository, {"openrouter": FakeOpenRouter()}, now=lambda: datetime(2026, 8, 10, tzinfo=UTC))
    service.refresh(context("user-a"), "openrouter")

    favorite = service.favorite(context("user-a"), "openrouter", "anthropic/claude-test", True)

    assert favorite.is_favorite is True
    assert service.list(context("user-a"), "openrouter", favorites_only=True)[0].model_id == "anthropic/claude-test"
    assert repository.configured[("user-a", "openrouter")]["api_key"] == "secret-for-user-a"
    with pytest.raises(LookupError):
        service.favorite(context("user-a"), "openrouter", "unknown/model", True)


def test_refreshes_omniroute_from_its_saved_gateway_url() -> None:
    repository = FakeRepository()
    repository.configured[("user-a", "omniroute")] = {
        "enabled": True, "api_key": "omni-secret", "base_url": "http://localhost:20128/v1",
    }

    class FakeOmniRoute:
        def fetch(self, api_key: str, *, base_url: str) -> list[dict[str, object]]:
            assert api_key == "omni-secret"
            assert base_url == "http://localhost:20128/v1"
            return [{"id": "auto/coding", "name": "Coding route", "route_kind": "auto", "supported_parameters": ["tools"]}]

    service = ProviderModelCatalogService(repository, {"omniroute": FakeOmniRoute()}, now=lambda: datetime(2026, 8, 10, tzinfo=UTC))

    receipt = service.refresh(context("user-a"), "omniroute")
    item = service.list(context("user-a"), "omniroute")[0]

    assert receipt.count == 1
    assert item.provider == "omniroute"
    assert item.model_id == "auto/coding"
    assert item.route_kind == "auto"
