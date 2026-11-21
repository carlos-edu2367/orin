"""Per-call provider/model/quota routing for child subagents (WS-A1)."""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentos.agentic.tools._shared import ToolOutcome

CHILD_PROVIDER_PINNED = "CHILD_PROVIDER_PINNED"
CHILD_PROVIDER_UNAVAILABLE = "CHILD_PROVIDER_UNAVAILABLE"
CHILD_MODEL_NOT_FAVORITE = "CHILD_MODEL_NOT_FAVORITE"
CHILD_QUOTA_BLOCKED = "CHILD_QUOTA_BLOCKED"


@dataclass(frozen=True, slots=True)
class ChildTarget:
    """Resolved execution target for a child subagent."""

    provider: str
    model_id: str | None
    model_chain: tuple[str, ...] = ()
    effort: str | None = None
    pinned: bool = False


def _extract_definition_pin(definition_pin: Any) -> tuple[str | None, tuple[str, ...]]:
    """Extract ``(pinned_provider, pinned_models)`` from a pin object, mapping, or provider string."""
    if definition_pin is None:
        return None, ()
    if isinstance(definition_pin, str):
        clean = definition_pin.strip()
        return (clean or None), ()
    if isinstance(definition_pin, Mapping):
        raw_provider = definition_pin.get("provider")
        raw_models = definition_pin.get("models") or ()
    else:
        raw_provider = getattr(definition_pin, "provider", None)
        raw_models = getattr(definition_pin, "models", ()) or ()
    provider = str(raw_provider).strip() if raw_provider is not None and str(raw_provider).strip() else None
    models = tuple(
        str(item).strip()
        for item in raw_models
        if isinstance(item, str) and item.strip()
    )
    return provider, models


def _favorites_for(
    favorites_by_provider: Mapping[str, Sequence[str]] | None,
    provider: str,
) -> tuple[str, ...]:
    """Return deduplicated non-batch favorite models for ``provider``."""
    if not favorites_by_provider or not provider:
        return ()
    raw = favorites_by_provider.get(provider)
    if raw is None:
        lower = provider.lower()
        for key, value in favorites_by_provider.items():
            if str(key).strip().lower() == lower:
                raw = value
                break
    if not raw:
        return ()
    seen: list[str] = []
    for item in raw:
        clean = str(item or "").strip()
        if not clean or clean.lower().endswith(":batch"):
            continue
        if clean not in seen:
            seen.append(clean)
    return tuple(seen)


def _match_favorite(requested_model: str, favorites: Sequence[str]) -> str | None:
    """Match ``requested_model`` against a provider's favorite list (exact, case-insensitive, or unique suffix)."""
    clean = str(requested_model or "").strip()
    if not clean or clean.lower().endswith(":batch"):
        return None
    if clean in favorites:
        return clean
    lower = clean.lower()
    for item in favorites:
        if item.lower() == lower:
            return item
    if "/" not in clean:
        suffix = f"/{lower}"
        matches = [item for item in favorites if item.lower().endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
    return None


def check_child_quota(
    quota_service: Any | None,
    *,
    user_id: str | None,
    provider: str,
    model_id: str | None = None,
    quota_policy_enabled: bool = False,
    quota_block_percent: int = 90,
    agent_name: str = "subagent",
) -> ToolOutcome | None:
    """Return a ``CHILD_QUOTA_BLOCKED`` outcome if policy is enabled and subscription quota is blocked."""
    if not quota_policy_enabled or quota_service is None:
        return None
    status_for = getattr(quota_service, "status_for", None)
    if not callable(status_for):
        return None
    status = status_for(str(user_id or ""), provider, model_id=model_id)
    rejected = bool(getattr(status, "rejected", False))
    max_percent = getattr(status, "max_percent", None)
    exceeded = max_percent is not None and float(max_percent) >= float(quota_block_percent)
    if not rejected and not exceeded:
        return None
    detail = (
        f"Provider '{provider}' subscription is currently rejected by rate limit."
        if rejected
        else f"Provider '{provider}' quota usage ({max_percent:.0f}%) reached the block threshold ({quota_block_percent}%)."
    )
    return ToolOutcome(
        "failed",
        f"Cota de {provider} bloqueada",
        f"Subagent '{agent_name}' cannot run on provider '{provider}': {detail}",
        {
            "agent_name": agent_name,
            "label": agent_name,
            "tool_kind": "agent",
            "provider": provider,
            "model_id": model_id,
            "quota_rejected": rejected,
            "quota_used_percent": max_percent,
            "quota_block_percent": int(quota_block_percent),
        },
        CHILD_QUOTA_BLOCKED,
    )


def resolve_child_target(
    requested_provider: str | None,
    requested_model: str | None,
    parent_provider: str,
    favorites_by_provider: Mapping[str, Sequence[str]] | None,
    definition_pin: Any | None = None,
    quota_service: Any | None = None,
    *,
    user_id: str | None = None,
    configured_providers: Collection[str] | None = None,
    quota_policy_enabled: bool = False,
    quota_block_percent: int = 90,
    effort: str | None = None,
    parent_model_id: str | None = None,
    agent_name: str = "subagent",
    allowed_hint: str = "",
    model_resolver: Callable[[str], str | None] | None = None,
    current_model_allowed: Callable[[str, bool], bool] | None = None,
) -> ChildTarget | ToolOutcome:
    """Resolve the target provider, model, model chain and effort for a child subagent call.

    When ``requested_provider`` is omitted/empty, preserves current single-provider behavior.
    When ``requested_provider`` is provided, validates against definition pins, configured providers,
    per-provider favorites, and (when ``quota_policy_enabled`` is True) subscription quota status.
    """
    req_provider = str(requested_provider or "").strip()
    req_model = str(requested_model or "").strip()
    parent_prov = str(parent_provider or "").strip()
    clean_effort = str(effort).strip().lower() if effort is not None and str(effort).strip() else None
    pin_provider, pin_models = _extract_definition_pin(definition_pin)

    if not req_provider:
        if pin_provider is not None or pin_models:
            effective_provider = pin_provider or parent_prov
            chosen = req_model or (pin_models[0] if len(pin_models) == 1 else None)
            return ChildTarget(
                provider=effective_provider,
                model_id=chosen,
                model_chain=pin_models,
                effort=clean_effort,
                pinned=True,
            )
        effective_provider = parent_prov
        if req_model:
            matched: str | None = None
            if model_resolver is not None:
                resolved = model_resolver(req_model)
                if resolved and (current_model_allowed is None or current_model_allowed(resolved, True)):
                    matched = resolved
            else:
                prov_favorites = _favorites_for(favorites_by_provider, effective_provider)
                matched = _match_favorite(req_model, prov_favorites)
                if matched is None and parent_model_id and req_model.lower() == str(parent_model_id).strip().lower():
                    matched = str(parent_model_id).strip()
            if not matched:
                return ToolOutcome(
                    "failed",
                    f"Modelo de {agent_name} nao permitido",
                    "This subagent is not configured with the current model or a current favorite model for this provider."
                    + allowed_hint,
                    {"agent_name": agent_name, "label": agent_name, "tool_kind": "agent"},
                    CHILD_MODEL_NOT_FAVORITE,
                )
            return ChildTarget(
                provider=effective_provider,
                model_id=matched,
                model_chain=(),
                effort=clean_effort,
                pinned=False,
            )
        clean_parent_model = str(parent_model_id).strip() if parent_model_id else None
        return ChildTarget(
            provider=effective_provider,
            model_id=clean_parent_model or None,
            model_chain=(),
            effort=clean_effort,
            pinned=False,
        )

    # Explicit requested_provider path
    if pin_provider is not None and pin_provider.lower() != req_provider.lower():
        return ToolOutcome(
            "failed",
            f"Provedor de {agent_name} fixo",
            f"Agent '{agent_name}' pins provider '{pin_provider}' and cannot run on '{req_provider}'.",
            {
                "agent_name": agent_name,
                "label": agent_name,
                "tool_kind": "agent",
                "provider": req_provider,
                "pinned_provider": pin_provider,
            },
            CHILD_PROVIDER_PINNED,
        )
    if pin_provider is None and pin_models and parent_prov and req_provider.lower() != parent_prov.lower():
        return ToolOutcome(
            "failed",
            f"Provedor de {agent_name} fixo",
            f"Agent '{agent_name}' pins models on provider '{parent_prov}' and cannot run on '{req_provider}'.",
            {
                "agent_name": agent_name,
                "label": agent_name,
                "tool_kind": "agent",
                "provider": req_provider,
                "pinned_provider": parent_prov,
            },
            CHILD_PROVIDER_PINNED,
        )

    if configured_providers is not None:
        configured_lower = {str(p).strip().lower() for p in configured_providers if str(p).strip()}
        is_configured = req_provider.lower() in configured_lower
    else:
        fav_keys_lower = {str(k).strip().lower() for k in (favorites_by_provider or {}) if str(k).strip()}
        is_configured = req_provider.lower() in fav_keys_lower or (
            bool(parent_prov) and req_provider.lower() == parent_prov.lower()
        )

    if not is_configured:
        return ToolOutcome(
            "failed",
            f"Provedor {req_provider} indisponivel",
            f"Provider '{req_provider}' is not configured or available for subagent '{agent_name}'.",
            {
                "agent_name": agent_name,
                "label": agent_name,
                "tool_kind": "agent",
                "provider": req_provider,
            },
            CHILD_PROVIDER_UNAVAILABLE,
        )

    prov_favorites = _favorites_for(favorites_by_provider, req_provider)
    if req_model:
        matched_model = _match_favorite(req_model, prov_favorites)
        if not matched_model:
            fav_hint = (
                f" Valid favorite models for '{req_provider}': {', '.join(prov_favorites)}."
                if prov_favorites
                else ""
            )
            return ToolOutcome(
                "failed",
                f"Modelo de {agent_name} nao permitido",
                f"Model '{req_model}' is not a favorite model for provider '{req_provider}'.{fav_hint}",
                {
                    "agent_name": agent_name,
                    "label": agent_name,
                    "tool_kind": "agent",
                    "provider": req_provider,
                    "model_id": req_model,
                    "available_models": list(prov_favorites),
                },
                CHILD_MODEL_NOT_FAVORITE,
            )
        chosen_model = matched_model
    else:
        if not prov_favorites:
            return ToolOutcome(
                "failed",
                f"Provedor {req_provider} sem modelos favoritos",
                f"Provider '{req_provider}' has no favorite models available for subagent '{agent_name}'.",
                {
                    "agent_name": agent_name,
                    "label": agent_name,
                    "tool_kind": "agent",
                    "provider": req_provider,
                },
                CHILD_PROVIDER_UNAVAILABLE,
            )
        chosen_model = prov_favorites[0]

    quota_blocked = check_child_quota(
        quota_service,
        user_id=user_id,
        provider=req_provider,
        model_id=chosen_model,
        quota_policy_enabled=quota_policy_enabled,
        quota_block_percent=quota_block_percent,
        agent_name=agent_name,
    )
    if quota_blocked is not None:
        return quota_blocked

    switched = req_provider.lower() != parent_prov.lower()
    return ChildTarget(
        provider=req_provider,
        model_id=chosen_model,
        model_chain=(chosen_model,) if switched else (),
        effort=clean_effort,
        pinned=switched,
    )


__all__ = [
    "CHILD_MODEL_NOT_FAVORITE",
    "CHILD_PROVIDER_PINNED",
    "CHILD_PROVIDER_UNAVAILABLE",
    "CHILD_QUOTA_BLOCKED",
    "ChildTarget",
    "check_child_quota",
    "resolve_child_target",
]
