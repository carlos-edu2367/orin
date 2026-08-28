from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

from .models import PROVIDERS_WITH_OPTIONAL_KEY, PricingSummary, ProviderCatalogContext, ProviderModelRecord, RefreshReceipt
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
        if not isinstance(api_key, str) or (not api_key and normalized_provider not in PROVIDERS_WITH_OPTIONAL_KEY):
            raise ProviderCatalogUnavailable("provider credential is unavailable")
        upstream = self._upstreams.get(normalized_provider)
        if upstream is None:
            raise ProviderCatalogUnavailable("provider catalog is not supported")
        try:
            raw_models = upstream.fetch(api_key, base_url=str(credential.get("base_url") or ""))
        except Exception as error:
            raise ProviderCatalogUnavailable("provider catalog refresh failed") from error
        refreshed_at = self._now()
        normalizer = _NORMALIZERS.get(normalized_provider, _normalize_openai)
        records = [normalizer(model, refreshed_at) for model in raw_models]
        # The repository stores one row per (user, provider, model_id); an upstream
        # catalog that lists the same id twice (OmniRoute is known to do this — see
        # tests/unit/provider_catalog/test_service.py) would otherwise violate that
        # constraint and fail the whole refresh instead of just dropping the repeat.
        deduplicated: dict[str, ProviderModelRecord] = {}
        for record in records:
            deduplicated[record.model_id] = record
        records = sorted(deduplicated.values(), key=lambda item: (item.display_name.casefold(), item.model_id))
        self._repository.replace(context, normalized_provider, records, refreshed_at)
        return RefreshReceipt(refreshed_at, len(records))

    def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False) -> list[ProviderModelRecord]:
        return self._repository.list(context, _provider(provider), favorites_only)

    def favorite(self, context: ProviderCatalogContext, provider: str, model_id: str, favorite: bool) -> ProviderModelRecord:
        if not isinstance(model_id, str) or not model_id.strip():
            raise LookupError("model_id is required")
        return self._repository.set_favorite(context, _provider(provider), model_id, favorite)

    def add_custom(self, context: ProviderCatalogContext, provider: str, model_id: str, display_name: str | None = None) -> ProviderModelRecord:
        normalized_provider = _provider(provider)
        normalized_id = _bounded_text(model_id, "model_id", 512)
        normalized_name = _bounded_text(display_name, "display_name", 512) if display_name is not None else normalized_id
        return self._repository.add_custom(
            context,
            normalized_provider,
            ProviderModelRecord(
                provider=normalized_provider, model_id=normalized_id, display_name=normalized_name,
                context_window=None, capabilities=(), input_modalities=("text",), output_modalities=("text",),
                pricing=None, refreshed_at=self._now(), is_custom=True,
            ),
        )

    def remove_custom(self, context: ProviderCatalogContext, provider: str, model_id: str) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise LookupError("model_id is required")
        self._repository.remove_custom(context, _provider(provider), model_id.strip())


def _provider(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"openai", "anthropic", "openrouter", "omniroute", "ollama"}:
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


def _normalize_openai(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    return ProviderModelRecord(
        provider="openai",
        model_id=model_id,
        display_name=_text(raw.get("name")) or model_id,
        context_window=None,
        capabilities=(),
        input_modalities=("text",),
        output_modalities=("text",),
        pricing=None,
        refreshed_at=refreshed_at,
    )


def _normalize_anthropic(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), Mapping) else {}
    capability_names = tuple(sorted(
        str(name) for name, value in capabilities.items()
        if isinstance(value, Mapping) and value.get("supported") is True
    ))
    max_input_tokens = raw.get("max_input_tokens")
    context_window = max_input_tokens if isinstance(max_input_tokens, int) and not isinstance(max_input_tokens, bool) and max_input_tokens > 0 else None
    input_modalities = ("text", "image") if "image_input" in capability_names else ("text",)
    return ProviderModelRecord(
        provider="anthropic",
        model_id=model_id,
        display_name=_text(raw.get("display_name")) or model_id,
        context_window=context_window,
        capabilities=capability_names,
        input_modalities=input_modalities,
        output_modalities=("text",),
        pricing=None,
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


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _bounded_text(value: object, name: str, maximum: int) -> str:
    text = _text(value)
    if text is None or len(text) > maximum:
        raise ValueError(f"{name} must be a bounded non-blank string")
    return text


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


_NORMALIZERS = {
    "anthropic": _normalize_anthropic,
    "openrouter": _normalize_openrouter,
    "openai": _normalize_openai,
    "omniroute": _normalize_omniroute,
    "ollama": _normalize_ollama,
}
