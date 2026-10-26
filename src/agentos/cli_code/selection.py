"""Which provider and model an ``orin code`` run uses.

``--model`` may be ``provider/model``, ``provider:model`` or a bare model id that exactly one enabled provider
offers; ``--provider`` names the provider explicitly. Without ``--model`` a resumed session keeps its conversation's
model, and a new one uses the ``model`` key of the person's own settings (the same key as Claude Code's), read the
same way. Nothing is guessed: an unknown or ambiguous model is an error that names what is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import CliRuntimeError

KNOWN_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "openrouter", "nvidia", "opencode", "ollama", "omniroute")


class _Api(Protocol):
    def get(self, path: str, **params: str) -> object: ...


@dataclass(frozen=True, slots=True)
class Selection:
    provider: str
    model_id: str

    def as_request(self) -> dict[str, str]:
        return {"provider": self.provider, "model_id": self.model_id}


class ModelCatalog:
    """Enabled providers and their models, asked of the API at most once each per run."""

    def __init__(self, api: _Api) -> None:
        self._api = api
        self._enabled: dict[str, bool] = {}
        self._models: dict[str, frozenset[str]] = {}

    def enabled(self, provider: str) -> bool:
        if provider not in self._enabled:
            try:
                info = self._api.get(f"/v1/providers/{provider}")
            except CliRuntimeError:
                info = None
            self._enabled[provider] = bool(isinstance(info, dict) and info.get("enabled"))
        return self._enabled[provider]

    def models(self, provider: str) -> frozenset[str]:
        if provider not in self._models:
            try:
                body = self._api.get(f"/v1/providers/{provider}/models")
            except CliRuntimeError:
                body = None
            items = body.get("items") if isinstance(body, dict) else None
            self._models[provider] = frozenset(
                str(item.get("model_id")) for item in items or () if isinstance(item, dict) and item.get("model_id")
            )
        return self._models[provider]

    def offers(self, provider: str, model_id: str) -> bool:
        return self.enabled(provider) and model_id in self.models(provider)

    def enabled_providers(self) -> list[str]:
        return [name for name in KNOWN_PROVIDERS if self.enabled(name)]


def _explicit(catalog: ModelCatalog, value: str) -> Selection | None:
    for separator in ("/", ":"):
        head, found, tail = value.partition(separator)
        if found and head.lower() in KNOWN_PROVIDERS and tail and catalog.offers(head.lower(), tail):
            return Selection(head.lower(), tail)
    return None


def resolve_model(catalog: ModelCatalog, value: str, provider: str | None = None) -> Selection:
    value = value.strip()
    if not value:
        raise CliRuntimeError("--model is empty")
    if provider:
        name = provider.strip().lower()
        if name not in KNOWN_PROVIDERS:
            raise CliRuntimeError(f"unknown provider {provider!r}; Orin knows {', '.join(KNOWN_PROVIDERS)}")
        if not catalog.enabled(name):
            raise CliRuntimeError(f"the provider {name} is not set up in Orin (Settings > Providers)")
        if catalog.offers(name, value):
            return Selection(name, value)
        explicit = _explicit(catalog, value)
        if explicit is not None and explicit.provider == name:
            return explicit
        raise CliRuntimeError(f"{name} does not offer the model {value!r}")
    explicit = _explicit(catalog, value)
    if explicit is not None:
        return explicit
    matches = [name for name in catalog.enabled_providers() if value in catalog.models(name)]
    if len(matches) == 1:
        return Selection(matches[0], value)
    if len(matches) > 1:
        raise CliRuntimeError(f"the model {value!r} is offered by {', '.join(matches)}; choose one with --provider")
    enabled = catalog.enabled_providers()
    where = f"enabled providers: {', '.join(enabled)}" if enabled else "no provider is set up in Orin (Settings > Providers)"
    raise CliRuntimeError(f"no enabled provider offers the model {value!r} ({where})")


def choose_selection(
    catalog: ModelCatalog,
    *,
    model: str | None,
    provider: str | None,
    remembered: Selection | None,
    configured: str | None,
) -> Selection:
    """``--model`` (with ``--provider``), else the resumed conversation's model, else the ``model`` setting."""
    if model:
        return resolve_model(catalog, model, provider)
    if remembered is not None and (provider is None or provider.strip().lower() == remembered.provider):
        return remembered
    if configured:
        return resolve_model(catalog, configured, provider)
    raise CliRuntimeError("choose a model with --model provider/model (or set \"model\" in ~/.orin/settings.json)")


__all__ = ["KNOWN_PROVIDERS", "ModelCatalog", "Selection", "choose_selection", "resolve_model"]
