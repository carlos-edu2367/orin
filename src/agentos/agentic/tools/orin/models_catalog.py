"""Model catalog inspection tools for AgentToolset (`list_models`)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Any, Iterable, Mapping

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec
from agentos.provider_catalog.models import (
    PROVIDERS_WITH_OPTIONAL_KEY,
    SUBSCRIPTION_CLI_PROVIDERS,
)

SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "google-antigravity",
    "openai-codex",
    "anthropic",
    "openrouter",
    "openai",
    "nvidia",
    "omniroute",
    "ollama",
    "opencode",
)


@dataclass(frozen=True, slots=True)
class _CatalogUserContext:
    user_id: str
    purpose: str = "list_models"


def _provider_kind(provider: str) -> str:
    clean = str(provider or "").strip().lower()
    if clean in SUBSCRIPTION_CLI_PROVIDERS:
        return "subscription_cli"
    if clean in {"ollama", "omniroute"}:
        return "local"
    return "api"


def _normalize_capabilities(
    raw_capabilities: Iterable[ Any] | Mapping[str, Any] | None,
    input_modalities: Iterable[Any] | None = None,
    *,
    item: Any = None,
) -> list[str]:
    caps_set: set[str] = set()
    if isinstance(raw_capabilities, Mapping):
        for k, v in raw_capabilities.items():
            if v:
                caps_set.add(str(k).strip().lower())
    elif isinstance(raw_capabilities, Iterable) and not isinstance(raw_capabilities, (str, bytes)):
        for entry in raw_capabilities:
            text = str(entry or "").strip().lower()
            if text:
                caps_set.add(text)
    modalities_set: set[str] = set()
    if isinstance(input_modalities, Iterable) and not isinstance(input_modalities, (str, bytes)):
        for entry in input_modalities:
            text = str(entry or "").strip().lower()
            if text:
                modalities_set.add(text)

    supports_tools = (
        bool(getattr(item, "supports_tools", False))
        or "tools" in caps_set
        or "tool_choice" in caps_set
        or "function_calling" in caps_set
    )
    supports_vision = (
        bool(getattr(item, "supports_vision", False))
        or "vision" in caps_set
        or "image" in modalities_set
    )
    supports_thinking = (
        bool(getattr(item, "supports_thinking", False))
        or "thinking" in caps_set
        or "reasoning" in caps_set
    )

    ordered: list[str] = []
    if supports_tools:
        ordered.append("tools")
    if supports_vision:
        ordered.append("vision")
    if supports_thinking:
        ordered.append("thinking")
    for extra in sorted(caps_set):
        if extra not in {"tools", "tool_choice", "function_calling", "vision", "thinking", "reasoning"} and extra not in ordered:
            ordered.append(extra)
    return ordered


def _format_price_value(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            return round(float(val), 6)
        except (TypeError, ValueError):
            return None
    if isinstance(val, str) and val.strip():
        try:
            return round(float(val.strip()), 6)
        except ValueError:
            return None
    return None


def _extract_pricing(item: Any) -> dict[str, float | None] | None:
    if isinstance(item, Mapping):
        raw_pricing = item.get("pricing")
        if isinstance(raw_pricing, Mapping):
            inp = _format_price_value(
                raw_pricing.get("input_per_million")
                if "input_per_million" in raw_pricing
                else raw_pricing.get("input")
            )
            out = _format_price_value(
                raw_pricing.get("output_per_million")
                if "output_per_million" in raw_pricing
                else raw_pricing.get("output")
            )
            if inp is not None or out is not None:
                return {"input_per_million": inp, "output_per_million": out}
        inp = _format_price_value(item.get("input_price_per_million"))
        out = _format_price_value(item.get("output_price_per_million"))
        if inp is not None or out is not None:
            return {"input_per_million": inp, "output_per_million": out}
        return None

    pricing_obj = getattr(item, "pricing", None)
    if pricing_obj is not None:
        if isinstance(pricing_obj, Mapping):
            inp = _format_price_value(
                pricing_obj.get("input_per_million")
                if "input_per_million" in pricing_obj
                else pricing_obj.get("input")
            )
            out = _format_price_value(
                pricing_obj.get("output_per_million")
                if "output_per_million" in pricing_obj
                else pricing_obj.get("output")
            )
        else:
            inp = _format_price_value(getattr(pricing_obj, "input_per_million", None))
            out = _format_price_value(getattr(pricing_obj, "output_per_million", None))
        if inp is not None or out is not None:
            return {"input_per_million": inp, "output_per_million": out}

    inp = _format_price_value(getattr(item, "input_price_per_million", None))
    out = _format_price_value(getattr(item, "output_price_per_million", None))
    if inp is not None or out is not None:
        return {"input_per_million": inp, "output_per_million": out}
    return None


def _serialize_model_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        provider = str(item.get("provider") or "").strip().lower()
        model_id = str(item.get("model_id") or item.get("id") or "").strip()
        if not provider or not model_id:
            return None
        display_name = str(item.get("display_name") or item.get("name") or model_id).strip() or model_id
        is_favorite = bool(item.get("is_favorite", False))
        kind = str(item.get("kind") or _provider_kind(provider)).strip()
        capabilities = _normalize_capabilities(
            item.get("capabilities"),
            item.get("input_modalities"),
            item=item,
        )
        raw_ctx = item.get("context_window")
        context_window = int(raw_ctx) if isinstance(raw_ctx, (int, float)) and not isinstance(raw_ctx, bool) and int(raw_ctx) > 0 else None
        pricing = _extract_pricing(item)
    else:
        provider = str(getattr(item, "provider", "") or "").strip().lower()
        model_id = str(getattr(item, "model_id", "") or getattr(item, "id", "") or "").strip()
        if not provider or not model_id:
            return None
        display_name = str(getattr(item, "display_name", "") or getattr(item, "name", "") or model_id).strip() or model_id
        is_favorite = bool(getattr(item, "is_favorite", False))
        kind = str(getattr(item, "kind", "") or _provider_kind(provider)).strip()
        capabilities = _normalize_capabilities(
            getattr(item, "capabilities", None),
            getattr(item, "input_modalities", None),
            item=item,
        )
        raw_ctx = getattr(item, "context_window", None)
        context_window = int(raw_ctx) if isinstance(raw_ctx, (int, float)) and not isinstance(raw_ctx, bool) and int(raw_ctx) > 0 else None
        pricing = _extract_pricing(item)

    return {
        "provider": provider,
        "model_id": model_id,
        "display_name": display_name,
        "is_favorite": is_favorite,
        "kind": kind,
        "capabilities": capabilities,
        "context_window": context_window,
        "pricing": pricing,
    }


def _matches_capability_filter(capabilities: list[str], capability: str) -> bool:
    target = capability.strip().lower()
    if not target:
        return True
    if target in {"thinking", "reasoning"}:
        return "thinking" in capabilities or "reasoning" in capabilities
    if target in {"vision", "image"}:
        return "vision" in capabilities or "image" in capabilities
    if target in {"tools", "tool_choice", "function_calling"}:
        return "tools" in capabilities
    return target in capabilities


class ModelCatalogTools:
    """Mixed into ``AgentToolset``; provides ``list_models`` and catalog helpers."""

    def _collect_user_catalog_models(self) -> list[dict[str, Any]]:
        user_id = str(
            getattr(self, "_project_user_id", None)
            or getattr(self, "_skill_user_id", None)
            or getattr(self, "_mcp_user_id", None)
            or "local-user"
        ).strip()
        catalog = getattr(self, "_provider_catalog", None)
        collected: list[dict[str, Any]] = []

        if catalog is not None:
            raw_items: Iterable[Any] | None = None
            if callable(catalog):
                raw_items = catalog(user_id)
            elif isinstance(catalog, Mapping) and "models" in catalog:
                raw_items = catalog.get("models")  # type: ignore[assignment]
            elif isinstance(catalog, (list, tuple, set)):
                raw_items = catalog
            elif callable(getattr(catalog, "list", None)):
                ctx = _CatalogUserContext(user_id=user_id)
                items_acc: list[Any] = []
                for prov in SUPPORTED_PROVIDERS:
                    try:
                        items_acc.extend(catalog.list(ctx, prov))
                    except Exception:
                        pass
                raw_items = items_acc
            elif callable(getattr(catalog, "list_models", None)):
                try:
                    res = catalog.list_models(_CatalogUserContext(user_id=user_id))
                    raw_items = res.get("models", ()) if isinstance(res, Mapping) else getattr(res, "items", res)
                except TypeError:
                    try:
                        res = catalog.list_models(user_id)
                        raw_items = res.get("models", ()) if isinstance(res, Mapping) else getattr(res, "items", res)
                    except TypeError:
                        res = catalog.list_models()
                        raw_items = res.get("models", ()) if isinstance(res, Mapping) else getattr(res, "items", res)
                except Exception:
                    raw_items = None

            if raw_items is not None:
                for entry in raw_items:
                    serialized = _serialize_model_item(entry)
                    if serialized is not None:
                        collected.append(serialized)

        engine = (
            getattr(catalog, "_engine", None)
            or getattr(getattr(catalog, "_repository", None), "_engine", None)
            or getattr(getattr(self, "_chat_store", None), "_engine", None)
            or getattr(getattr(self, "_project_store", None), "_engine", None)
        )

        if not collected and engine is not None:
            try:
                from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository

                repo = PostgresProviderCatalogRepository(engine)
                ctx = _CatalogUserContext(user_id=user_id)
                for prov in SUPPORTED_PROVIDERS:
                    for row in repo.list(ctx, prov):
                        serialized = _serialize_model_item(row)
                        if serialized is not None:
                            collected.append(serialized)
            except Exception:
                pass

        has_config_rows = False
        enabled_configs: dict[str, dict[str, Any]] = {}
        if engine is not None:
            try:
                from sqlalchemy import select
                from agentos.persistence.postgres.schema import (
                    provider_api_keys,
                    provider_configurations,
                    provider_model_catalog,
                    provider_model_favorites,
                )
                from agentos.provider_catalog.models import (
                    PROVIDERS_WITH_OPTIONAL_KEY,
                    SUBSCRIPTION_CLI_PROVIDERS,
                )
                from agentos.providers.cli_registry import cli_adapter_for

                with engine.connect() as conn:
                    cfg_rows = (
                        conn.execute(
                            select(
                                provider_configurations.c.provider,
                                provider_configurations.c.enabled,
                                provider_configurations.c.base_url,
                                provider_configurations.c.secret_ref,
                                provider_configurations.c.risk_acknowledged_at,
                            ).where(provider_configurations.c.user_id == user_id)
                        )
                        .mappings()
                        .all()
                    )
                    if cfg_rows:
                        has_config_rows = True
                        active_key_providers = {
                            str(r["provider"]).strip().lower()
                            for r in conn.execute(
                                select(provider_api_keys.c.provider).where(
                                    provider_api_keys.c.user_id == user_id,
                                    provider_api_keys.c.status == "active",
                                    provider_api_keys.c.api_key_ciphertext.is_not(None),
                                )
                            )
                            .mappings()
                            .all()
                            if r.get("provider")
                        }
                        catalog_providers = {
                            str(r["provider"]).strip().lower()
                            for r in conn.execute(
                                select(provider_model_catalog.c.provider)
                                .where(provider_model_catalog.c.user_id == user_id)
                                .distinct()
                            )
                            .mappings()
                            .all()
                            if r.get("provider")
                        }
                        for row in cfg_rows:
                            if not bool(row.get("enabled")):
                                continue
                            prov = str(row.get("provider") or "").strip().lower()
                            if not prov:
                                continue
                            if prov == "google-antigravity" and row.get("risk_acknowledged_at") is None:
                                continue
                            requires_key = (
                                prov not in PROVIDERS_WITH_OPTIONAL_KEY
                                and prov not in SUBSCRIPTION_CLI_PROVIDERS
                            )
                            has_secret = bool(str(row.get("secret_ref") or "").strip()) or (
                                prov in active_key_providers
                            )
                            if requires_key and not has_secret and prov not in catalog_providers:
                                continue
                            enabled_configs[prov] = dict(row)

                        collected = [m for m in collected if m.get("provider") in enabled_configs]

                        for prov, cfg in enabled_configs.items():
                            if any(m.get("provider") == prov for m in collected):
                                continue
                            adapter = cli_adapter_for(
                                prov,
                                cfg.get("base_url"),
                                risk_acknowledged_at=cfg.get("risk_acknowledged_at"),
                            )
                            if adapter is None:
                                continue
                            fav_ids = {
                                str(r["model_id"])
                                for r in conn.execute(
                                    select(provider_model_favorites.c.model_id).where(
                                        provider_model_favorites.c.user_id == user_id,
                                        provider_model_favorites.c.provider == prov,
                                    )
                                )
                                .mappings()
                                .all()
                                if r.get("model_id")
                            }
                            try:
                                cli_models = list(adapter.models())
                            except Exception:
                                cli_models = []
                            for idx, m_obj in enumerate(cli_models):
                                serialized = _serialize_model_item(m_obj, default_provider=prov)
                                if serialized is not None:
                                    if serialized["model_id"] in fav_ids or (not fav_ids and idx == 0):
                                        serialized["is_favorite"] = True
                                    collected.append(serialized)
            except Exception:
                pass

        default_prov = str(getattr(self, "_default_provider", "") or "").strip().lower()
        default_mod = str(getattr(self, "_default_model_id", "") or "").strip()
        if default_prov and default_mod and (not has_config_rows or default_prov in enabled_configs):
            if not any(m["provider"] == default_prov and m["model_id"] == default_mod for m in collected):
                collected.append(
                    {
                        "provider": default_prov,
                        "model_id": default_mod,
                        "display_name": default_mod,
                        "is_favorite": True,
                        "kind": _provider_kind(default_prov),
                        "capabilities": ["tools"],
                        "context_window": None,
                        "pricing": None,
                    }
                )

        collected.sort(
            key=lambda m: (
                0 if m.get("is_favorite") else 1,
                str(m.get("provider") or ""),
                str(m.get("display_name") or "").lower(),
                str(m.get("model_id") or ""),
            )
        )
        return collected

    def list_models(
        self,
        provider: str | None = None,
        favorites_only: bool = False,
        capability: str | None = None,
        query: str | None = None,
        limit: int = 60,
    ) -> ToolOutcome:
        clean_provider = str(provider or "").strip().lower() if provider is not None else ""
        if clean_provider and clean_provider not in SUPPORTED_PROVIDERS:
            # Still allow custom provider names in tests if present in catalog, otherwise validate
            all_models = self._collect_user_catalog_models()
            known_providers = {m["provider"] for m in all_models} | set(SUPPORTED_PROVIDERS)
            if clean_provider not in known_providers:
                raise AgentToolError(
                    f"Unsupported provider '{clean_provider}'. Known providers: {sorted(known_providers)}."
                )
        else:
            all_models = self._collect_user_catalog_models()

        if isinstance(limit, bool):
            raise AgentToolError("limit must be an integer between 1 and 200.")
        try:
            effective_limit = min(max(int(limit), 1), 200)
        except (TypeError, ValueError) as error:
            raise AgentToolError("limit must be an integer between 1 and 200.") from error

        clean_capability = str(capability or "").strip().lower() if capability is not None else ""
        clean_query = str(query or "").strip().lower() if query is not None else ""
        only_favs = bool(favorites_only)

        filtered: list[dict[str, Any]] = []
        for item in all_models:
            if clean_provider and item["provider"] != clean_provider:
                continue
            if only_favs and not item.get("is_favorite"):
                continue
            if clean_capability and not _matches_capability_filter(item.get("capabilities", []), clean_capability):
                continue
            if clean_query:
                haystack = f"{item['provider']} {item['model_id']} {item['display_name']}".lower()
                if clean_query not in haystack:
                    continue
            filtered.append(item)

        total_matched = len(filtered)
        models_slice = filtered[:effective_limit]
        providers_summary: dict[str, dict[str, Any]] = {}
        for item in models_slice:
            prov = item["provider"]
            entry = providers_summary.setdefault(
                prov,
                {
                    "provider": prov,
                    "kind": item["kind"],
                    "count": 0,
                    "favorites_count": 0,
                },
            )
            entry["count"] += 1
            if item.get("is_favorite"):
                entry["favorites_count"] += 1

        body = {
            "count": len(models_slice),
            "total_matched": total_matched,
            "providers": list(providers_summary.values()),
            "models": models_slice,
        }
        payload = {
            **body,
            "tool_kind": "model_catalog",
        }
        return ToolOutcome(
            "succeeded",
            f"{len(models_slice)} modelo(s) disponível(is) listado(s)",
            json.dumps(body, ensure_ascii=False),
            payload,
        )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="list_models",
        description=(
            "List configured AI providers and models available to this user (including subscription CLI providers "
            "like google-antigravity and openai-codex, API providers like anthropic, openrouter, openai, nvidia, "
            "and local providers like ollama/omniroute) with capabilities, favorites, context window, and pricing."
        ),
        legacy_schema=_schema(
            {
                "provider": {
                    **_TEXT,
                    "description": "Optional provider slug filter (e.g. 'anthropic', 'openai-codex', 'google-antigravity', 'openrouter', 'openai', 'ollama').",
                },
                "favorites_only": {
                    "type": "boolean",
                    "description": "When true, return only models marked as favorites by the user (default false).",
                },
                "capability": {
                    **_TEXT,
                    "description": "Optional capability filter ('tools', 'vision', or 'thinking').",
                },
                "query": {
                    **_TEXT,
                    "description": "Optional case-insensitive substring filter over model_id or display_name.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum number of models to return (default 60).",
                },
            }
        ),
        handler="list_models",
        kind="model_catalog",
        read_only=True,
        available=lambda self: True,
        order=555,
    ),
)
