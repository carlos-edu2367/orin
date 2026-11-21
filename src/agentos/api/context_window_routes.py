"""HTTP routes for effective model context windows (`GET /v1/context/windows`, `DELETE /v1/context/windows/{provider}/{model_id}`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.providers.claude_cli.catalog import (
    _SUBSCRIPTION_MODELS,
    cached_cli_context_window_for,
    discover_cached_models,
    fixed_context_window_for,
)
from agentos.providers.claude_cli.effective_window import (
    MAX_OVERRIDE_TOKENS,
    MIN_OVERRIDE_TOKENS,
    effective_window_for,
)
from agentos.quota.claude_account import ClaudeAccountReader
from agentos.settings.loader import load_effective_settings
from agentos.settings.registry import resolve

from .security import AuthenticatedPrincipal

_CLAUDE_CLI_PROVIDERS: frozenset[str] = frozenset({"anthropic", "anthropic-cli"})


def _load_window_settings(project_root: Path | None) -> tuple[dict[str, int], bool]:
    try:
        settings = load_effective_settings(project_root, trusted=project_root is not None)
        raw_overrides = resolve(settings, "context.windowOverrides").value
        overrides: dict[str, int] = {}
        if isinstance(raw_overrides, Mapping):
            for k, v in raw_overrides.items():
                if isinstance(k, str) and k.strip() and isinstance(v, int) and not isinstance(v, bool):
                    if MIN_OVERRIDE_TOKENS <= v <= MAX_OVERRIDE_TOKENS:
                        overrides[k.strip()] = int(v)
        long_ctx = bool(resolve(settings, "claudeCli.longContextWithCredits").value)
        return overrides, long_ctx
    except Exception:  # noqa: BLE001 - settings load must never break the route
        return {}, False


def _override_for_model(
    provider: str,
    model_id: str,
    overrides: Mapping[str, int],
) -> int | None:
    if not overrides:
        return None
    bare = str(model_id or "").strip().rsplit("/", 1)[-1].strip()
    lowered = {k.lower(): v for k, v in overrides.items()}
    for candidate in (f"{provider}/{bare}", f"{provider}/{model_id}", bare, model_id):
        val = overrides.get(candidate)
        if val is None:
            val = lowered.get(candidate.lower())
        if isinstance(val, int) and not isinstance(val, bool):
            return int(val)
    return None


def _resolve_observation_store(services: Any) -> Any | None:
    store = getattr(services, "context_observations", None)
    if store is not None and not store.__class__.__name__.startswith("_Unavailable"):
        return store
    quota_service = getattr(services, "quota_service", None)
    qstore = getattr(quota_service, "_store", None) if quota_service is not None else None
    engine = getattr(qstore, "_engine", None)
    if engine is not None:
        from agentos.persistence.postgres.context_observations import ContextObservationStore

        return ContextObservationStore(engine)
    return None


def _resolve_account_reader(services: Any) -> ClaudeAccountReader:
    reader = getattr(services, "claude_account_reader", None)
    if reader is not None and hasattr(reader, "read"):
        return reader
    return ClaudeAccountReader()


def _collect_models(
    services: Any,
    user_id: str,
    observations: Mapping[tuple[str, str], Any],
    overrides: Mapping[str, int],
) -> list[dict[str, Any]]:
    entries: dict[tuple[str, str], dict[str, Any]] = {}

    catalog = getattr(services, "provider_catalog", None)
    if catalog is not None and not catalog.__class__.__name__.startswith("_Unavailable"):
        list_fn = getattr(catalog, "list", None)
        if callable(list_fn):
            try:
                rows = list_fn(ProviderCatalogContext(user_id, "context.windows.read"), None, False)
            except TypeError:
                try:
                    rows = list_fn(ProviderCatalogContext(user_id, "context.windows.read"))
                except Exception:  # noqa: BLE001
                    rows = []
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows or ():
                if isinstance(row, Mapping):
                    prov = str(row.get("provider") or "").strip()
                    mdl = str(row.get("model_id") or row.get("id") or "").strip()
                    name = str(row.get("display_name") or row.get("name") or mdl).strip()
                    win = row.get("context_window") or row.get("context_length")
                else:
                    prov = str(getattr(row, "provider", "") or "").strip()
                    mdl = str(getattr(row, "model_id", "") or getattr(row, "id", "") or "").strip()
                    name = str(getattr(row, "display_name", "") or getattr(row, "name", "") or mdl).strip()
                    win = getattr(row, "context_window", None) or getattr(row, "context_length", None)
                if not prov or not mdl:
                    continue
                win_int = int(win) if isinstance(win, int) and not isinstance(win, bool) and win > 0 else None
                entries[(prov, mdl)] = {
                    "provider": prov,
                    "model_id": mdl,
                    "display_name": name or mdl,
                    "catalog_tokens": win_int,
                }

    # Include known Claude CLI subscription models if none were populated from the DB catalog
    if not any(prov in _CLAUDE_CLI_PROVIDERS for prov, _ in entries):
        for cached in discover_cached_models():
            key = ("anthropic", cached.id)
            entries.setdefault(
                key,
                {
                    "provider": "anthropic",
                    "model_id": cached.id,
                    "display_name": cached.name,
                    "catalog_tokens": cached.context_window,
                },
            )
        for model_id, display_name, window in _SUBSCRIPTION_MODELS:
            key = ("anthropic", model_id)
            entries.setdefault(
                key,
                {
                    "provider": "anthropic",
                    "model_id": model_id,
                    "display_name": display_name,
                    "catalog_tokens": window,
                },
            )

    # Ensure any model with a learned observation or manual override appears in the list
    for (prov, mdl) in observations:
        entries.setdefault(
            (prov, mdl),
            {
                "provider": prov,
                "model_id": mdl,
                "display_name": mdl,
                "catalog_tokens": None,
            },
        )
    for raw_key in overrides:
        if "/" in raw_key:
            prov, mdl = raw_key.split("/", 1)
            prov, mdl = prov.strip(), mdl.strip()
            if prov and mdl:
                entries.setdefault(
                    (prov, mdl),
                    {
                        "provider": prov,
                        "model_id": mdl,
                        "display_name": mdl,
                        "catalog_tokens": None,
                    },
                )

    return sorted(entries.values(), key=lambda item: (item["provider"], item["model_id"]))


def register_context_window_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
) -> None:
    """Register `GET /v1/context/windows` and `DELETE /v1/context/windows/{provider}/{model_id:path}`."""

    @app.get("/v1/context/windows")
    async def list_context_windows(
        request: Request,
        project_root: str | None = Query(default=None, max_length=4096),
    ) -> JSONResponse:
        def _load() -> dict[str, Any]:
            principal = principal_for(request)
            services.security.check_rate_limit(
                principal,
                action="context.windows.read",
                origin=request.headers.get("origin"),
            )
            root_path = Path(project_root) if project_root else None
            overrides, long_ctx = _load_window_settings(root_path)
            obs_store = _resolve_observation_store(services)
            obs_list = obs_store.list(principal.user_id) if obs_store is not None else []
            obs_by_key = {(o.provider, o.model_id): o for o in obs_list}

            account_state = _resolve_account_reader(services).read()
            models = _collect_models(services, principal.user_id, obs_by_key, overrides)

            results: list[dict[str, Any]] = []
            for item in models:
                prov = item["provider"]
                mdl = item["model_id"]
                obs = obs_by_key.get((prov, mdl))
                is_claude = prov.lower() in _CLAUDE_CLI_PROVIDERS
                cli_win = (
                    (item["catalog_tokens"] or cached_cli_context_window_for(mdl))
                    if is_claude
                    else None
                )
                fixed_win = (
                    fixed_context_window_for(mdl)
                    if is_claude
                    else item["catalog_tokens"]
                )
                catalog_tokens = cli_win or fixed_win or item["catalog_tokens"]
                ew = effective_window_for(
                    prov,
                    mdl,
                    window_overrides=overrides,
                    learned_limit=obs.observed_limit_tokens if obs is not None else None,
                    learned_source=obs.source if obs is not None else None,
                    account_state=account_state if is_claude else None,
                    long_context_with_credits=long_ctx if is_claude else False,
                    cli_catalog_window=cli_win,
                    fixed_catalog_window=fixed_win,
                )
                override_tokens = _override_for_model(prov, mdl, overrides)
                results.append(
                    {
                        "provider": prov,
                        "model_id": mdl,
                        "display_name": item["display_name"],
                        "catalog_tokens": catalog_tokens,
                        "learned": obs.to_public_dict() if obs is not None else None,
                        "override_tokens": override_tokens,
                        "effective_tokens": ew.tokens,
                        "effective_source": ew.source,
                        "effective_detail": ew.detail,
                    }
                )
            return {"windows": results}

        payload = await run_in_threadpool(_load)
        return JSONResponse(payload)

    @app.delete("/v1/context/windows/{provider}/{model_id:path}")
    async def forget_context_window(
        provider: str,
        model_id: str,
        request: Request,
    ) -> JSONResponse:
        def _forget() -> dict[str, Any]:
            principal = principal_for(request)
            services.security.check_rate_limit(
                principal,
                action="context.windows.write",
                origin=request.headers.get("origin"),
            )
            obs_store = _resolve_observation_store(services)
            forgotten = False
            if obs_store is not None:
                forgotten = bool(obs_store.invalidate(principal.user_id, provider, model_id))
            return {
                "forgotten": forgotten,
                "provider": provider,
                "model_id": model_id,
            }

        payload = await run_in_threadpool(_forget)
        return JSONResponse(payload)


__all__ = ["register_context_window_routes"]
