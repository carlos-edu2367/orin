"""Verifier model selection policy and deterministic fallback chain (WS-A2, spec 5.3)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentos.agentic.effort_levels import EffortAdjustment, clamp
from agentos.agentic.model_tiers import (
    family_of,
    normalize_model_id,
    strip_effort_suffix,
    tier_of,
)
from agentos.quota.models import Confidence, QuotaStatus
from agentos.quota.normalize import normalize_provider_name

VALID_VERIFIER_POLICIES: tuple[str, ...] = ("cross_family", "same_model", "explicit")


@dataclass(frozen=True, slots=True)
class AuthorModel:
    provider: str
    model: str
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class AvailableVerifierModel:
    provider: str
    model: str
    configured: bool = True
    is_favorite: bool = True
    capabilities: tuple[str, ...] = ()
    quota_status: QuotaStatus | None = None


@dataclass(frozen=True, slots=True)
class ExplicitVerifierTarget:
    provider: str
    model: str
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class VerifierChoice:
    provider: str | None
    model: str | None
    effort: str | None
    policy: str
    fallback_step: int
    reason: str
    effort_adjustment: EffortAdjustment | None = None

    @property
    def available(self) -> bool:
        return bool(self.provider and self.model and self.fallback_step < 4)

    def to_event_payload(self) -> dict[str, Any]:
        return verifier_model_selected_payload(self)


def verifier_model_selected_payload(choice: VerifierChoice) -> dict[str, Any]:
    """Build the ``verifier.model_selected`` event payload (spec 5.3)."""
    chosen = (
        {
            "provider": choice.provider,
            "model": choice.model,
            "effort": choice.effort,
        }
        if choice.provider and choice.model
        else None
    )
    return {
        "policy": choice.policy,
        "chosen": chosen,
        "fallback_step": choice.fallback_step,
        "reason": choice.reason,
    }


def _coerce_author(author: AuthorModel | Mapping[str, Any]) -> AuthorModel:
    if isinstance(author, AuthorModel):
        return author
    if isinstance(author, Mapping):
        provider = str(author.get("provider") or "").strip()
        model = str(author.get("model") or author.get("model_id") or "").strip()
        raw_effort = author.get("effort")
        effort = str(raw_effort).strip().lower() if isinstance(raw_effort, str) and raw_effort.strip() else None
        return AuthorModel(provider=provider, model=model, effort=effort)
    raise TypeError("author must be AuthorModel or Mapping")


def _coerce_candidate(item: AvailableVerifierModel | Mapping[str, Any]) -> AvailableVerifierModel:
    if isinstance(item, AvailableVerifierModel):
        return item
    if isinstance(item, Mapping):
        provider = str(item.get("provider") or "").strip()
        model = str(item.get("model") or item.get("model_id") or "").strip()
        configured = bool(item.get("configured", True))
        if "is_favorite" in item:
            is_favorite = bool(item["is_favorite"])
        else:
            is_favorite = bool(item.get("favorite", True))
        raw_caps = item.get("capabilities") or ()
        capabilities = tuple(str(c).strip().lower() for c in raw_caps if str(c).strip())
        quota_status = _coerce_quota_status(item.get("quota_status"))
        return AvailableVerifierModel(
            provider=provider,
            model=model,
            configured=configured,
            is_favorite=is_favorite,
            capabilities=capabilities,
            quota_status=quota_status,
        )
    raise TypeError("available items must be AvailableVerifierModel or Mapping")


def _coerce_explicit(
    explicit: ExplicitVerifierTarget | Mapping[str, Any] | None,
) -> ExplicitVerifierTarget | None:
    if explicit is None:
        return None
    if isinstance(explicit, ExplicitVerifierTarget):
        if not explicit.provider.strip() or not explicit.model.strip():
            return None
        return explicit
    if isinstance(explicit, Mapping):
        provider = str(explicit.get("provider") or "").strip()
        model = str(explicit.get("model") or explicit.get("model_id") or "").strip()
        if not provider or not model:
            return None
        raw_effort = explicit.get("effort")
        effort = str(raw_effort).strip().lower() if isinstance(raw_effort, str) and raw_effort.strip() else None
        return ExplicitVerifierTarget(provider=provider, model=model, effort=effort)
    return None


def _coerce_quota_status(raw: Any) -> QuotaStatus | None:
    if raw is None:
        return None
    if isinstance(raw, QuotaStatus):
        return raw
    if isinstance(raw, Mapping):
        raw_pct = raw.get("max_percent")
        max_percent = float(raw_pct) if isinstance(raw_pct, (int, float)) and not isinstance(raw_pct, bool) else None
        rejected = bool(raw.get("rejected", False))
        raw_conf = raw.get("confidence", Confidence.UNKNOWN)
        try:
            confidence = raw_conf if isinstance(raw_conf, Confidence) else Confidence(str(raw_conf))
        except ValueError:
            confidence = Confidence.UNKNOWN
        return QuotaStatus(
            max_percent=max_percent,
            rejected=rejected,
            confidence=confidence,
            observed_at=raw.get("observed_at"),
            resets_at=raw.get("resets_at"),
        )
    return None


def _lookup_quota(
    quota: Any,
    provider: str,
    model: str,
    candidate_status: QuotaStatus | None = None,
) -> QuotaStatus | None:
    if candidate_status is not None:
        return candidate_status
    if quota is None:
        return None

    canon_provider = normalize_provider_name(provider)
    clean_model = normalize_model_id(provider, model)

    if callable(quota):
        return _coerce_quota_status(quota(provider, model))

    if isinstance(quota, Mapping):
        for key in (
            (provider, model),
            (canon_provider, clean_model),
            f"{provider}/{model}",
            f"{canon_provider}/{clean_model}",
            provider,
            canon_provider,
        ):
            if key in quota:
                return _coerce_quota_status(quota[key])
        return None

    status_fn = getattr(quota, "status_for", None)
    if callable(status_fn):
        try:
            return _coerce_quota_status(status_fn(provider, model))
        except TypeError:
            return None
    return None


@dataclass(frozen=True, slots=True)
class _EvaluatedCandidate:
    index: int
    candidate: AvailableVerifierModel
    canon_provider: str
    clean_model: str
    base_model: str
    family: str
    tier: int
    quota_status: QuotaStatus | None
    available: bool
    unavailable_reason: str | None


def _evaluate_candidates(
    available: Sequence[AvailableVerifierModel | Mapping[str, Any]],
    *,
    quota: Any,
    tiers: Mapping[str, Any] | None,
    quota_policy_enabled: bool,
    quota_block_percent: int | float,
) -> list[_EvaluatedCandidate]:
    evaluated: list[_EvaluatedCandidate] = []
    for idx, raw_item in enumerate(available):
        cand = _coerce_candidate(raw_item)
        if not cand.provider or not cand.model:
            continue
        canon_provider = normalize_provider_name(cand.provider)
        clean_model = normalize_model_id(cand.provider, cand.model)
        base_model = strip_effort_suffix(clean_model)
        fam = family_of(cand.provider, cand.model, tiers=tiers)
        tier = tier_of(cand.provider, cand.model, tiers=tiers)
        q_status = _lookup_quota(quota, cand.provider, cand.model, cand.quota_status)

        unavail_reason: str | None = None
        if not cand.configured:
            unavail_reason = "provider_not_configured"
        elif not cand.is_favorite:
            unavail_reason = "model_not_favorite"
        elif q_status is not None and q_status.rejected:
            unavail_reason = "quota_rejected"
        elif (
            quota_policy_enabled
            and q_status is not None
            and q_status.max_percent is not None
            and q_status.max_percent >= quota_block_percent
        ):
            unavail_reason = "quota_blocked"

        evaluated.append(
            _EvaluatedCandidate(
                index=idx,
                candidate=cand,
                canon_provider=canon_provider,
                clean_model=clean_model,
                base_model=base_model,
                family=fam,
                tier=tier,
                quota_status=q_status,
                available=unavail_reason is None,
                unavailable_reason=unavail_reason,
            )
        )
    return evaluated


def _quota_sort_key(status: QuotaStatus | None) -> tuple[int, float]:
    """Known quota usage sorts ahead of unknown, ascending by max_percent."""
    if status is not None and status.max_percent is not None:
        return (0, float(status.max_percent))
    return (1, 0.0)


def _rank_candidates(items: Sequence[_EvaluatedCandidate]) -> _EvaluatedCandidate:
    """Tie-break: lowest valid tier (>= author) first, then lowest known quota, then input order."""
    return min(
        items,
        key=lambda item: (item.tier, _quota_sort_key(item.quota_status), item.index),
    )


def _build_choice(
    item: _EvaluatedCandidate,
    *,
    policy: str,
    fallback_step: int,
    reason: str,
    requested_effort: str | None,
) -> VerifierChoice:
    applied_effort, adjustment = clamp(
        item.candidate.provider,
        item.candidate.model,
        requested_effort,
        item.candidate.capabilities,
    )
    return VerifierChoice(
        provider=item.candidate.provider,
        model=item.candidate.model,
        effort=applied_effort,
        policy=policy,
        fallback_step=fallback_step,
        reason=reason,
        effort_adjustment=adjustment,
    )


def _find_exact_candidate(
    evaluated: Sequence[_EvaluatedCandidate],
    provider: str,
    model: str,
) -> _EvaluatedCandidate | None:
    target_prov = normalize_provider_name(provider)
    target_model = normalize_model_id(provider, model)
    for item in evaluated:
        if item.canon_provider == target_prov and item.clean_model == target_model:
            return item
    return None


def _run_fallback_chain(
    *,
    author_prov: str,
    author_clean: str,
    author_base: str,
    author_family: str,
    min_tier: int,
    evaluated: Sequence[_EvaluatedCandidate],
    policy: str,
    requested_effort: str | None,
    reason_prefix: str = "",
) -> VerifierChoice:
    def _compose_reason(base_reason: str, note: str = "") -> str:
        text = f"{reason_prefix}:{base_reason}" if reason_prefix else base_reason
        if note:
            text = f"{text} ({note})"
        return text

    # Step 1: Another family with tier >= max(author_tier, 1) and tier > 0.
    step1_all = [
        item
        for item in evaluated
        if item.family != author_family and item.tier > 0 and item.tier >= min_tier
    ]
    step1_avail = [item for item in step1_all if item.available]
    step1_quota_skipped = any(
        item.unavailable_reason in {"quota_blocked", "quota_rejected"} for item in step1_all
    )
    if step1_avail:
        chosen = _rank_candidates(step1_avail)
        note = "skipped_quota_blocked" if step1_quota_skipped else ""
        return _build_choice(
            chosen,
            policy=policy,
            fallback_step=1,
            reason=_compose_reason("cross_family_match", note),
            requested_effort=requested_effort,
        )

    # Step 2: Same family, different model (different base name), tier >= max(author_tier, 1) and tier > 0.
    step2_all = [
        item
        for item in evaluated
        if item.family == author_family
        and item.base_model != author_base
        and item.tier > 0
        and item.tier >= min_tier
    ]
    step2_avail = [item for item in step2_all if item.available]
    if step2_avail:
        chosen = _rank_candidates(step2_avail)
        note = "cross_family_quota_blocked" if step1_quota_skipped else ""
        return _build_choice(
            chosen,
            policy=policy,
            fallback_step=2,
            reason=_compose_reason("fallback_same_family_other_model", note),
            requested_effort=requested_effort,
        )

    # Step 3: Same provider and model as author, fresh context, clamped high (or requested) effort.
    author_entry = _find_exact_candidate(evaluated, author_prov, author_clean)
    if author_entry is not None and author_entry.available:
        note = "cross_family_quota_blocked" if step1_quota_skipped else ""
        step3_effort = requested_effort or "high"
        return _build_choice(
            author_entry,
            policy=policy,
            fallback_step=3,
            reason=_compose_reason("fallback_same_model_fresh_context", note),
            requested_effort=step3_effort,
        )

    # Step 4: No verifier available (all subscriptions blocked or unconfigured).
    all_quota_blocked = bool(evaluated) and all(
        item.unavailable_reason in {"quota_blocked", "quota_rejected"} for item in evaluated
    )
    note = "all_quota_blocked" if all_quota_blocked else ""
    return VerifierChoice(
        provider=None,
        model=None,
        effort=None,
        policy=policy,
        fallback_step=4,
        reason=_compose_reason("no_available_verifier", note),
        effort_adjustment=None,
    )


def select_verifier(
    author: AuthorModel | Mapping[str, Any],
    available: Sequence[AvailableVerifierModel | Mapping[str, Any]],
    policy: str = "cross_family",
    explicit: ExplicitVerifierTarget | Mapping[str, Any] | None = None,
    quota: Mapping[Any, Any] | Callable[[str, str], QuotaStatus | None] | Any | None = None,
    tiers: Mapping[str, Any] | None = None,
    requested_effort: str = "high",
    quota_policy_enabled: bool = False,
    quota_block_percent: int | float = 90,
) -> VerifierChoice:
    """Choose the verifier model according to policy and fallback rules (spec 5.3)."""
    norm_policy = str(policy or "").strip().lower()
    if norm_policy not in VALID_VERIFIER_POLICIES:
        allowed = ", ".join(VALID_VERIFIER_POLICIES)
        raise ValueError(f"Política de verificador desconhecida: '{policy}' (esperado: {allowed})")

    author_obj = _coerce_author(author)
    author_prov = normalize_provider_name(author_obj.provider)
    author_clean = normalize_model_id(author_obj.provider, author_obj.model)
    author_base = strip_effort_suffix(author_clean)
    author_family = family_of(author_obj.provider, author_obj.model, tiers=tiers)
    author_tier = tier_of(author_obj.provider, author_obj.model, tiers=tiers)
    min_tier = max(author_tier, 1)

    evaluated = _evaluate_candidates(
        available,
        quota=quota,
        tiers=tiers,
        quota_policy_enabled=quota_policy_enabled,
        quota_block_percent=quota_block_percent,
    )

    if norm_policy == "same_model":
        author_entry = _find_exact_candidate(evaluated, author_obj.provider, author_obj.model)
        if author_entry is not None and author_entry.available:
            return _build_choice(
                author_entry,
                policy=norm_policy,
                fallback_step=1,
                reason="same_model_policy",
                requested_effort=requested_effort,
            )
        unavail = author_entry.unavailable_reason if author_entry is not None else "author_not_available"
        return _run_fallback_chain(
            author_prov=author_prov,
            author_clean=author_clean,
            author_base=author_base,
            author_family=author_family,
            min_tier=min_tier,
            evaluated=evaluated,
            policy=norm_policy,
            requested_effort=requested_effort,
            reason_prefix=f"same_model_unavailable({unavail})",
        )

    if norm_policy == "explicit":
        target = _coerce_explicit(explicit)
        if target is not None:
            entry = _find_exact_candidate(evaluated, target.provider, target.model)
            if entry is not None and entry.available:
                eff = target.effort if target.effort is not None else requested_effort
                return _build_choice(
                    entry,
                    policy=norm_policy,
                    fallback_step=1,
                    reason="explicit_match",
                    requested_effort=eff,
                )
            unavail = entry.unavailable_reason if entry is not None else "target_not_found"
            reason_prefix = f"explicit_unavailable({unavail})"
        else:
            reason_prefix = "explicit_missing"

        return _run_fallback_chain(
            author_prov=author_prov,
            author_clean=author_clean,
            author_base=author_base,
            author_family=author_family,
            min_tier=min_tier,
            evaluated=evaluated,
            policy=norm_policy,
            requested_effort=requested_effort,
            reason_prefix=reason_prefix,
        )

    # Default: cross_family
    return _run_fallback_chain(
        author_prov=author_prov,
        author_clean=author_clean,
        author_base=author_base,
        author_family=author_family,
        min_tier=min_tier,
        evaluated=evaluated,
        policy=norm_policy,
        requested_effort=requested_effort,
    )


__all__ = [
    "AuthorModel",
    "AvailableVerifierModel",
    "ExplicitVerifierTarget",
    "VALID_VERIFIER_POLICIES",
    "VerifierChoice",
    "select_verifier",
    "verifier_model_selected_payload",
]
