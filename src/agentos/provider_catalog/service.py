from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

from .models import PricingSummary, ProviderCatalogContext, ProviderModelRecord, RefreshReceipt
from .ports import ProviderCatalogRepository, ProviderCatalogUpstream


class ProviderCatalogUnavailable(RuntimeError):
    """A sanitized failure to refresh a credential-scoped model catalog."""


class ProviderModelCatalogService:
    def __init__(
        self,
        repository: ProviderCatalogRepository,
        upstreams: Mapping[str, ProviderCatalogUpstream],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._upstreams = dict(upstreams)
        self._now = now

    def refresh(self, context: ProviderCatalogContext, provider: str) -> RefreshReceipt:
        normalized_provider = _provider(provider)
        credential = self._repository.credential(context, normalized_provider)
        if credential is None or credential.get("enabled") is not True:
            raise ProviderCatalogUnavailable("provider is not configured")
        api_key = credential.get("api_key")
        if not isinstance(api_key, str) or (not api_key and normalized_provider != "omniroute"):
            raise ProviderCatalogUnavailable("provider credential is unavailable")
        upstream = self._upstreams.get(normalized_provider)
        if upstream is None:
            raise ProviderCatalogUnavailable("provider catalog is not supported")
        try:
            raw_models = (
                upstream.fetch(api_key, base_url=str(credential.get("base_url") or ""))
                if normalized_provider == "omniroute"
                else upstream.fetch(api_key)
            )
        except Exception as error:
            raise ProviderCatalogUnavailable("provider catalog refresh failed") from error
        refreshed_at = self._now()
        normalizer = _normalize_omniroute if normalized_provider == "omniroute" else _normalize_openrouter
        records = [normalizer(model, refreshed_at) for model in raw_models]
        records.sort(key=lambda item: (item.display_name.casefold(), item.model_id))
        self._repository.replace(context, normalized_provider, records, refreshed_at)
        return RefreshReceipt(refreshed_at, len(records))

    def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False) -> list[ProviderModelRecord]:
        return self._repository.list(context, _provider(provider), favorites_only)

    def favorite(self, context: ProviderCatalogContext, provider: str, model_id: str, favorite: bool) -> ProviderModelRecord:
        if not isinstance(model_id, str) or not model_id.strip():
            raise LookupError("model_id is required")
        return self._repository.set_favorite(context, _provider(provider), model_id, favorite)


def _provider(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"openai", "anthropic", "openrouter", "omniroute"}:
        raise ProviderCatalogUnavailable("unsupported provider")
    return normalized


def _normalize_openrouter(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    display_name = _text(raw.get("name")) or model_id
    architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    context_window = raw.get("context_length")
    if isinstance(context_window, bool) or not isinstance(context_window, int) or context_window < 1:
        context_window = None
    return ProviderModelRecord(
        provider="openrouter",
        model_id=model_id,
        display_name=display_name,
        context_window=context_window,
        capabilities=tuple(sorted({_text(value) for value in _list(raw.get("supported_parameters")) if _text(value)})),
        input_modalities=_modalities(architecture.get("input_modalities")),
        output_modalities=_modalities(architecture.get("output_modalities")),
        pricing=_pricing(pricing),
        refreshed_at=refreshed_at,
    )


def _normalize_omniroute(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    context_window = raw.get("context_length")
    if isinstance(context_window, bool) or not isinstance(context_window, int) or context_window < 1:
        context_window = None
    route_kind = raw.get("route_kind")
    return ProviderModelRecord(
        provider="omniroute",
        model_id=model_id,
        display_name=_text(raw.get("name")) or model_id,
        context_window=context_window,
        capabilities=tuple(sorted({_text(value) for value in _list(raw.get("supported_parameters")) if _text(value)})),
        input_modalities=_modalities(raw.get("input_modalities")),
        output_modalities=_modalities(raw.get("output_modalities")),
        pricing=None,
        refreshed_at=refreshed_at,
        route_kind="auto" if route_kind == "auto" else "model",
    )


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _modalities(value: object) -> tuple[str, ...]:
    values: list[str] = []
    for item in _list(value):
        normalized = _text(item)
        if normalized is not None and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _pricing(raw: Mapping[str, object]) -> PricingSummary | None:
    input_price = _per_million(raw.get("prompt"))
    output_price = _per_million(raw.get("completion"))
    return PricingSummary(input_price, output_price) if input_price is not None or output_price is not None else None


def _per_million(value: object) -> str | None:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    try:
        converted = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError):
        return None
    if converted < 0:
        return None
    return format(converted.normalize(), "f").rstrip("0").rstrip(".") or "0"
