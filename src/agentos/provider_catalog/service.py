from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Sequence

import httpx

from .modalities import VALID_OUTPUT_MODALITIES, infer_output_modalities
from .models import PROVIDERS_WITH_OPTIONAL_KEY, PricingSummary, ProviderCatalogContext, ProviderModelRecord, RefreshReceipt
from .nvidia import (
    DEFAULT_NVIDIA_BASE_URL,
    NvidiaAuthenticationError,
    NvidiaModelCatalogClient,
    is_nvidia_cloud,
    normalize_nvidia_base_url,
)
from .ports import ProviderCatalogRepository, ProviderCatalogUpstream


_QUOTA_EXHAUSTION_MARKERS = (
    "insufficient_quota",
    "quota exceeded",
    "out of credits",
    "credit balance",
    "billing_hard_limit_reached",
    "exhausted",
    "payment required",
    "not enough credits",
)


def _classify_catalog_key_error(error: Exception) -> str | None:
    if isinstance(error, NvidiaAuthenticationError):
        return "exhausted"
    if type(error).__name__ in {"OpenCodeAuthenticationError", "OllamaCloudAuthenticationError"}:
        return "exhausted"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 402, 403):
            return "exhausted"
        if status == 429:
            body_text = ""
            try:
                body_text = error.response.text.lower()
            except Exception:
                body_text = ""
            if any(marker in body_text for marker in _QUOTA_EXHAUSTION_MARKERS):
                return "exhausted"
            return "cooldown"
        return None
    status_attr = getattr(error, "status_code", None)
    if getattr(error, "exhausted", False) is True or status_attr in (401, 402, 403):
        return "exhausted"
    if status_attr == 429:
        return "cooldown"
    return None


class ProviderCatalogUnavailable(RuntimeError):
    """A sanitized failure to refresh a credential-scoped model catalog."""


class ProviderModelCatalogService:
    def __init__(
        self,
        repository: ProviderCatalogRepository,
        upstreams: Mapping[str, ProviderCatalogUpstream] | None = None,
        *,
        nvidia_client: NvidiaModelCatalogClient | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._upstreams = dict(upstreams or {})
        self._nvidia_client = nvidia_client or NvidiaModelCatalogClient()
        self._now = now

    def _fetch_provider_records(
        self,
        normalized_provider: str,
        api_key: str,
        base_url_raw: object,
        refreshed_at: datetime,
    ) -> list[ProviderModelRecord]:
        if normalized_provider == "nvidia":
            base_url = normalize_nvidia_base_url(str(base_url_raw or DEFAULT_NVIDIA_BASE_URL))
            if is_nvidia_cloud(base_url) and not api_key.strip():
                raise ValueError("provider API key is required for NVIDIA NIM Cloud")
            upstream = self._upstreams.get("nvidia")
            if upstream is not None and hasattr(upstream, "fetch_models"):
                raw_payload = upstream.fetch_models(api_key, base_url=base_url)  # type: ignore[attr-defined]
            elif upstream is not None:
                raw_payload = {"data": upstream.fetch(api_key, base_url=base_url)}
            else:
                raw_payload = self._nvidia_client.fetch_models(api_key, base_url=base_url)
            return self._normalize("nvidia", raw_payload, refreshed_at)

        upstream = self._upstreams.get(normalized_provider)
        if upstream is None:
            raise ProviderCatalogUnavailable("provider catalog is not supported")
        raw_models = upstream.fetch(api_key, base_url=str(base_url_raw or ""))
        normalizer = _NORMALIZERS.get(normalized_provider, _normalize_openai)
        return [normalizer(model, refreshed_at) for model in raw_models]

    def refresh(self, context: ProviderCatalogContext, provider: str) -> RefreshReceipt:
        normalized_provider = _provider(provider)
        credential = self._repository.credential(context, normalized_provider)
        if credential is None or credential.get("enabled") is not True:
            raise ProviderCatalogUnavailable("provider is not configured")
        api_key = credential.get("api_key")
        if not isinstance(api_key, str) or (not api_key and normalized_provider not in PROVIDERS_WITH_OPTIONAL_KEY):
            raise ProviderCatalogUnavailable("provider credential is unavailable")

        base_url_raw = credential.get("base_url")
        if normalized_provider == "nvidia":
            normalized_base = normalize_nvidia_base_url(str(base_url_raw or DEFAULT_NVIDIA_BASE_URL))
            if is_nvidia_cloud(normalized_base) and not api_key.strip():
                raise ValueError("provider API key is required for NVIDIA NIM Cloud")
        elif normalized_provider not in self._upstreams:
            raise ProviderCatalogUnavailable("provider catalog is not supported")

        refreshed_at = self._now()
        next_key_fn = getattr(self._repository, "next_available_key", None)
        first_pool_key = next_key_fn(context.user_id, normalized_provider) if callable(next_key_fn) else None

        if first_pool_key is not None and callable(next_key_fn):
            tried: set[int] = set()
            current_key = first_pool_key
            while True:
                tried.add(current_key.id)
                try:
                    records = self._fetch_provider_records(
                        normalized_provider,
                        current_key.plaintext,
                        base_url_raw,
                        refreshed_at,
                    )
                    mark_active_fn = getattr(self._repository, "mark_active", None)
                    if callable(mark_active_fn):
                        mark_active_fn(current_key.id)
                    break
                except ProviderCatalogUnavailable:
                    raise
                except Exception as error:
                    classification = _classify_catalog_key_error(error)
                    if classification == "exhausted":
                        mark_exhausted_fn = getattr(self._repository, "mark_exhausted", None)
                        if callable(mark_exhausted_fn):
                            mark_exhausted_fn(current_key.id)
                    elif classification == "cooldown":
                        mark_cooldown_fn = getattr(self._repository, "mark_cooldown", None)
                        cooldown_fn = getattr(self._repository, "cooldown_seconds", None)
                        cooldown_secs = cooldown_fn(context, normalized_provider) if callable(cooldown_fn) else 60
                        if callable(mark_cooldown_fn):
                            mark_cooldown_fn(current_key.id, cooldown_secs)
                    else:
                        raise ProviderCatalogUnavailable("provider catalog refresh failed") from error

                    next_key = next_key_fn(context.user_id, normalized_provider, exclude=frozenset(tried))
                    if next_key is None:
                        raise ProviderCatalogUnavailable("provider catalog refresh failed") from error
                    current_key = next_key
        else:
            try:
                records = self._fetch_provider_records(normalized_provider, api_key, base_url_raw, refreshed_at)
            except ProviderCatalogUnavailable:
                raise
            except Exception as error:
                raise ProviderCatalogUnavailable("provider catalog refresh failed") from error

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

    def _normalize(
        self,
        provider: str,
        raw_payload: object,
        refreshed_at: datetime | None = None,
    ) -> list[ProviderModelRecord]:
        ts = refreshed_at if refreshed_at is not None else getattr(self, "_now", lambda: datetime.now(UTC))()
        normalized_provider = _provider(provider)
        if normalized_provider == "nvidia":
            return self._normalize_nvidia(raw_payload, ts)
        normalizer = _NORMALIZERS.get(normalized_provider, _normalize_openai)
        if isinstance(raw_payload, Mapping):
            raw_data = raw_payload.get("data")
            items = raw_data if isinstance(raw_data, list) else []
        elif isinstance(raw_payload, list):
            items = raw_payload
        else:
            items = []
        return [normalizer(dict(item), ts) for item in items if isinstance(item, Mapping)]

    def _normalize_nvidia(
        self,
        raw_payload: object,
        refreshed_at: datetime | None = None,
    ) -> list[ProviderModelRecord]:
        ts = refreshed_at if refreshed_at is not None else getattr(self, "_now", lambda: datetime.now(UTC))()
        if isinstance(raw_payload, Mapping):
            raw_data = raw_payload.get("data")
            items = raw_data if isinstance(raw_data, list) else []
        elif isinstance(raw_payload, list):
            items = raw_payload
        else:
            items = []
        records: list[ProviderModelRecord] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            records.append(_normalize_nvidia(dict(item), ts))
        return records

    def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False) -> list[ProviderModelRecord]:
        return self._repository.list(context, _provider(provider), favorites_only)

    def favorite(self, context: ProviderCatalogContext, provider: str, model_id: str, favorite: bool) -> ProviderModelRecord:
        if not isinstance(model_id, str) or not model_id.strip():
            raise LookupError("model_id is required")
        return self._repository.set_favorite(context, _provider(provider), model_id, favorite)

    def add_custom(
        self,
        context: ProviderCatalogContext,
        provider: str,
        model_id: str,
        display_name: str | None = None,
        output_modalities: Sequence[str] | None = None,
    ) -> ProviderModelRecord:
        normalized_provider = _provider(provider)
        normalized_id = _bounded_text(model_id, "model_id", 512)
        normalized_name = _bounded_text(display_name, "display_name", 512) if display_name is not None else normalized_id
        explicit: list[str] = []
        if output_modalities is not None:
            if isinstance(output_modalities, str) or not isinstance(output_modalities, Sequence):
                raise ValueError("output_modalities must be a sequence of valid modalities")
            for item in output_modalities:
                norm = str(item or "").strip().lower()
                if norm not in VALID_OUTPUT_MODALITIES:
                    raise ValueError(f"output_modalities contains unsupported modality: {item!r}")
                if norm not in explicit:
                    explicit.append(norm)
        resolved_output = tuple(explicit) if explicit else infer_output_modalities(normalized_id)
        return self._repository.add_custom(
            context,
            normalized_provider,
            ProviderModelRecord(
                provider=normalized_provider, model_id=normalized_id, display_name=normalized_name,
                context_window=None, capabilities=(), input_modalities=("text",), output_modalities=resolved_output,
                pricing=None, refreshed_at=self._now(), is_custom=True,
            ),
        )

    def remove_custom(self, context: ProviderCatalogContext, provider: str, model_id: str) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise LookupError("model_id is required")
        self._repository.remove_custom(context, _provider(provider), model_id.strip())


def _provider(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"openai", "anthropic", "openrouter", "opencode", "omniroute", "ollama", "nvidia"}:
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
        output_modalities=infer_output_modalities(model_id, _modalities(architecture.get("output_modalities"))),
        pricing=_pricing(pricing),
        refreshed_at=refreshed_at,
    )


def _normalize_opencode(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    display_name = _text(raw.get("name")) or model_id
    architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    context_window = raw.get("context_length")
    if isinstance(context_window, bool) or not isinstance(context_window, int) or context_window < 1:
        context_window = None
    input_modalities = _modalities(raw.get("input_modalities")) or _modalities(architecture.get("input_modalities")) or ("text",)
    output_modalities = _modalities(raw.get("output_modalities")) or _modalities(architecture.get("output_modalities"))
    return ProviderModelRecord(
        provider="opencode",
        model_id=model_id,
        display_name=display_name,
        context_window=context_window,
        capabilities=tuple(sorted({_text(value) for value in _list(raw.get("supported_parameters")) if _text(value)})),
        input_modalities=input_modalities,
        output_modalities=infer_output_modalities(model_id, output_modalities),
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
        output_modalities=infer_output_modalities(model_id),
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
        output_modalities=infer_output_modalities(model_id),
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
        output_modalities=infer_output_modalities(model_id, _modalities(raw.get("output_modalities"))),
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
        output_modalities=infer_output_modalities(model_id),
        # Local inference is free, and the hosted API publishes no per-token price.
        pricing=None,
        refreshed_at=refreshed_at,
    )


_NVIDIA_THINKING_MARKERS = ("deepseek-r1", "qwq", "thinking", "nemotron-ultra", "reasoning")
_NVIDIA_VISION_MARKERS = ("vision", "vl", "vlm", "pixtral", "cosmos", "neva", "paligemma")


def _normalize_nvidia(raw: dict[str, object], refreshed_at: datetime) -> ProviderModelRecord:
    model_id = _text(raw.get("id"))
    if model_id is None:
        raise ProviderCatalogUnavailable("provider returned an invalid model")
    display_name = _text(raw.get("name")) or model_id
    context_window = raw.get("context_length") or raw.get("max_model_len") or raw.get("context_window")
    if isinstance(context_window, bool) or not isinstance(context_window, int) or context_window < 1:
        context_window = None
    lower_id = model_id.lower()
    raw_caps = {
        str(val).strip().lower()
        for val in (*_list(raw.get("supported_parameters")), *_list(raw.get("capabilities")))
        if _text(val)
    }
    caps: set[str] = {"tools"} | raw_caps
    if any(marker in lower_id for marker in _NVIDIA_THINKING_MARKERS):
        caps.add("reasoning")
    is_vision = any(marker in lower_id for marker in _NVIDIA_VISION_MARKERS) or "vision" in raw_caps
    if is_vision:
        caps.add("vision")
    explicit_inputs = _modalities(raw.get("input_modalities"))
    if explicit_inputs:
        input_modalities = explicit_inputs if not is_vision or "image" in explicit_inputs else (*explicit_inputs, "image")
    else:
        input_modalities = ("text", "image") if is_vision else ("text",)
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    return ProviderModelRecord(
        provider="nvidia",
        model_id=model_id,
        display_name=display_name,
        context_window=context_window,
        capabilities=tuple(sorted(caps)),
        input_modalities=input_modalities,
        output_modalities=infer_output_modalities(model_id, _modalities(raw.get("output_modalities"))),
        pricing=_pricing(pricing),
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
    "opencode": _normalize_opencode,
    "openai": _normalize_openai,
    "omniroute": _normalize_omniroute,
    "ollama": _normalize_ollama,
    "nvidia": _normalize_nvidia,
}
