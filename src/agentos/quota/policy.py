"""Optional quota policy: warn thresholds, background-work gating, and between-turn fallback."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agentos.settings.loader import EffectiveSettings, SettingsLayer
from agentos.settings.registry import resolve as resolve_setting

from .models import QuotaStatus
from .normalize import normalize_provider_name


class QuotaStatusReader(Protocol):
    def status_for(
        self,
        user_id: str,
        provider: str,
        model_id: str | None = None,
    ) -> QuotaStatus: ...


@dataclass(frozen=True, slots=True)
class QuotaWarnDecision:
    should_warn: bool
    used_percent: float | None = None
    threshold: int = 80
    rejected: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class QuotaBackgroundDecision:
    allowed: bool
    used_percent: float | None = None
    threshold: int = 90
    rejected: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class QuotaFallbackDecision:
    from_provider: str
    from_model: str
    to_provider: str
    to_model: str
    reason: str = "quota_rejected"


def _coerce_effective_settings(
    settings: EffectiveSettings | Mapping[str, Any] | None,
) -> EffectiveSettings:
    if isinstance(settings, EffectiveSettings):
        return settings
    if isinstance(settings, Mapping):
        data = dict(settings)
        return EffectiveSettings(
            data=data,
            provenance={},
            layers=(SettingsLayer(name="user", path=None, data=data),),
            errors=(),
            trusted=True,
        )
    return EffectiveSettings(data={}, provenance={}, layers=(), errors=(), trusted=True)


def _parse_chain_entry(entry: str) -> tuple[str, str] | None:
    text = str(entry or "").strip()
    if not text or "/" not in text:
        return None
    provider, model = text.split("/", 1)
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        return None
    return provider, model


class QuotaPolicy:
    """Evaluates optional user-configured quota rules (disabled by default)."""

    def __init__(self, quota_service: QuotaStatusReader) -> None:
        self._quota_service = quota_service

    def _is_enabled(self, effective: EffectiveSettings) -> bool:
        return bool(resolve_setting(effective, "quota.policy.enabled").value)

    def warn(
        self,
        user_id: str,
        provider: str,
        *,
        model_id: str | None = None,
        settings: EffectiveSettings | Mapping[str, Any] | None = None,
    ) -> QuotaWarnDecision:
        effective = _coerce_effective_settings(settings)
        threshold = int(resolve_setting(effective, "quota.policy.warnPercent").value)
        if not self._is_enabled(effective):
            return QuotaWarnDecision(should_warn=False, threshold=threshold)

        status = self._quota_service.status_for(user_id, provider, model_id=model_id)
        if status.rejected:
            return QuotaWarnDecision(
                should_warn=True,
                used_percent=status.max_percent,
                threshold=threshold,
                rejected=True,
                reason="quota_rejected",
            )
        if status.max_percent is not None and status.max_percent >= threshold:
            return QuotaWarnDecision(
                should_warn=True,
                used_percent=status.max_percent,
                threshold=threshold,
                rejected=False,
                reason="warn_threshold_exceeded",
            )
        return QuotaWarnDecision(
            should_warn=False,
            used_percent=status.max_percent,
            threshold=threshold,
        )

    def check_background(
        self,
        user_id: str,
        provider: str,
        *,
        model_id: str | None = None,
        settings: EffectiveSettings | Mapping[str, Any] | None = None,
    ) -> QuotaBackgroundDecision:
        effective = _coerce_effective_settings(settings)
        threshold = int(resolve_setting(effective, "quota.policy.blockBackgroundPercent").value)
        if not self._is_enabled(effective):
            return QuotaBackgroundDecision(allowed=True, threshold=threshold)

        status = self._quota_service.status_for(user_id, provider, model_id=model_id)
        if status.rejected:
            return QuotaBackgroundDecision(
                allowed=False,
                used_percent=status.max_percent,
                threshold=threshold,
                rejected=True,
                reason="quota_rejected",
            )
        if status.max_percent is not None and status.max_percent >= threshold:
            return QuotaBackgroundDecision(
                allowed=False,
                used_percent=status.max_percent,
                threshold=threshold,
                rejected=False,
                reason="background_threshold_exceeded",
            )
        return QuotaBackgroundDecision(
            allowed=True,
            used_percent=status.max_percent,
            threshold=threshold,
        )

    def select_fallback(
        self,
        user_id: str,
        current_provider: str,
        current_model: str,
        *,
        settings: EffectiveSettings | Mapping[str, Any] | None = None,
        has_pending_tool_effects: bool = False,
        emit_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> QuotaFallbackDecision | None:
        """Select the next non-rejected `provider/model` in `quota.fallback.chain` between turns.

        Never switches mid-turn when `has_pending_tool_effects` is True.
        """
        if has_pending_tool_effects:
            return None

        effective = _coerce_effective_settings(settings)
        if not self._is_enabled(effective):
            return None
        if not bool(resolve_setting(effective, "quota.fallback.enabled").value):
            return None

        chain_raw = resolve_setting(effective, "quota.fallback.chain").value
        if not isinstance(chain_raw, list) or not chain_raw:
            return None

        current_canon = normalize_provider_name(current_provider)
        current_status = self._quota_service.status_for(
            user_id, current_canon, model_id=current_model
        )
        if not current_status.rejected:
            return None

        for raw_entry in chain_raw:
            parsed = _parse_chain_entry(str(raw_entry))
            if parsed is None:
                continue
            cand_provider_raw, cand_model = parsed
            cand_canon = normalize_provider_name(cand_provider_raw)
            if cand_canon == current_canon and cand_model == current_model:
                continue
            cand_status = self._quota_service.status_for(
                user_id, cand_canon, model_id=cand_model
            )
            if cand_status.rejected:
                continue

            decision = QuotaFallbackDecision(
                from_provider=current_provider,
                from_model=current_model,
                to_provider=cand_provider_raw,
                to_model=cand_model,
                reason="quota_rejected",
            )
            if callable(emit_event):
                emit_event(
                    "quota.fallback_applied",
                    {
                        "from_provider": decision.from_provider,
                        "from_model": decision.from_model,
                        "to_provider": decision.to_provider,
                        "to_model": decision.to_model,
                        "reason": decision.reason,
                    },
                )
            return decision

        return None


__all__ = [
    "QuotaBackgroundDecision",
    "QuotaFallbackDecision",
    "QuotaPolicy",
    "QuotaStatusReader",
    "QuotaWarnDecision",
]
