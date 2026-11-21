"""Unit tests for verifier model selection policy and fallback chain (WS-A2, spec 5.3)."""

from __future__ import annotations

import pytest

from agentos.agentic.agent_definitions import VERIFIER
from agentos.agentic.verifier_policy import (
    AuthorModel,
    AvailableVerifierModel,
    VerifierChoice,
    select_verifier,
    verifier_model_selected_payload,
)
from agentos.quota.models import Confidence, QuotaStatus


def _quota(max_percent: float | None = None, *, rejected: bool = False) -> QuotaStatus:
    confidence = Confidence.MEASURED if max_percent is not None else Confidence.UNKNOWN
    return QuotaStatus(
        max_percent=max_percent,
        rejected=rejected,
        confidence=confidence,
        observed_at=None,
    )


def test_builtin_verifier_has_no_pinned_model_or_provider() -> None:
    assert VERIFIER.name == "verifier"
    assert VERIFIER.provider is None
    assert VERIFIER.model is None
    assert VERIFIER.models == ()


def test_cross_family_picks_other_family_with_tier_gte_author() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5", effort="high")
    available = [
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
        AvailableVerifierModel(provider="google-antigravity", model="claude-opus-5-5-high"),
        AvailableVerifierModel(provider="openai-codex", model="gpt-6-luna"),  # tier 2 (< 4)
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),  # tier 4 (>= 4)
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-carbon-high"),  # tier 3 (< 4)
    ]

    choice = select_verifier(author, available, policy="cross_family")
    assert isinstance(choice, VerifierChoice)
    assert choice.provider == "openai-codex"
    assert choice.model == "gpt-6.1-sol"
    assert choice.effort == "high"
    assert choice.policy == "cross_family"
    assert choice.fallback_step == 1
    assert choice.available is True

    payload = verifier_model_selected_payload(choice)
    assert payload == {
        "policy": "cross_family",
        "chosen": {"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "high"},
        "fallback_step": 1,
        "reason": choice.reason,
    }


def test_antigravity_claude_does_not_count_as_other_family_for_claude_author() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")
    available = [
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
        AvailableVerifierModel(provider="google-antigravity", model="claude-opus-5-5-high"),
    ]

    choice = select_verifier(author, available, policy="cross_family")
    # Must NOT pick google-antigravity/claude-opus-5-5-high as step 1 (still anthropic)
    # and must NOT treat it as a different model in step 2 (same base claude-opus-5-5).
    assert choice.fallback_step == 3
    assert choice.provider == "anthropic-cli"
    assert choice.model == "claude-opus-5-5"
    assert choice.effort == "high"


def test_tier_zero_models_are_never_chosen_in_cross_family_or_step_2() -> None:
    author = AuthorModel(provider="openrouter", model="acme/unknown-author")
    available = [
        AvailableVerifierModel(provider="openrouter", model="acme/unknown-author"),
        AvailableVerifierModel(provider="openrouter", model="beta/unknown-reviewer"),  # tier 0
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-4-6"),  # tier 0
    ]

    choice = select_verifier(author, available, policy="cross_family")
    # Even when author itself is tier 0, tier-0 candidates must never be selected in steps 1 or 2.
    assert choice.fallback_step == 3
    assert choice.provider == "openrouter"
    assert choice.model == "acme/unknown-author"


def test_tie_break_prefers_equal_tier_over_higher_tier_then_lower_known_quota() -> None:
    author = AuthorModel(provider="openai-codex", model="gpt-6.1-sol")  # openai, tier 4
    available = [
        AvailableVerifierModel(provider="anthropic-cli", model="claude-fable-5-2"),  # tier 5
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-argon-high"),  # tier 4, quota unknown
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),  # tier 4, quota 35%
    ]
    quotas = {
        "anthropic-cli": _quota(35.0),
        "google-antigravity": _quota(None),
    }

    choice = select_verifier(author, available, policy="cross_family", quota=quotas)
    assert choice.fallback_step == 1
    # Tier 4 beats Tier 5 (claude-fable-5-2); known quota 35% beats unknown quota
    assert choice.provider == "anthropic-cli"
    assert choice.model == "claude-opus-5-5"

    # Now compare two known quotas at the same tier: lower usage wins
    author_claude = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")
    available_two = [
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),  # tier 4
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-hydrogen-high"),  # tier 4
    ]
    quotas_two = {
        "openai-codex": _quota(72.0),
        "google-antigravity": _quota(18.0),
    }
    choice_two = select_verifier(author_claude, available_two, policy="cross_family", quota=quotas_two)
    assert choice_two.fallback_step == 1
    assert choice_two.provider == "google-antigravity"
    assert choice_two.model == "gemini-4-hydrogen-high"


def test_tie_break_between_higher_tiers_prefers_smallest_valid_tier() -> None:
    author = AuthorModel(provider="openai-codex", model="gpt-6-astra")  # openai, tier 3
    available = [
        AvailableVerifierModel(provider="anthropic-cli", model="claude-fable-5-2"),  # tier 5
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),  # tier 4
    ]
    choice = select_verifier(author, available, policy="cross_family")
    assert choice.fallback_step == 1
    assert choice.model == "claude-opus-5-5"


def test_availability_filters_unconfigured_non_favorite_rejected_and_quota_policy() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")
    available = [
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol", configured=False),
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-terra", is_favorite=False),
        AvailableVerifierModel(provider="openai-codex", model="gpt-6-sol"),  # quota 92%
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-argon-high"),  # quota rejected
        AvailableVerifierModel(provider="anthropic-cli", model="claude-fable-5-2"),  # same family, tier 5
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
    ]
    quotas = {
        "openai-codex": _quota(92.0, rejected=False),
        "google-antigravity": _quota(None, rejected=True),
        "anthropic-cli": _quota(20.0, rejected=False),
    }

    # With quota_policy_enabled=False, 92% (not rejected) is still available!
    choice_off = select_verifier(
        author,
        available,
        policy="cross_family",
        quota=quotas,
        quota_policy_enabled=False,
        quota_block_percent=90,
    )
    assert choice_off.fallback_step == 1
    assert choice_off.provider == "openai-codex"
    assert choice_off.model == "gpt-6-sol"

    # With quota_policy_enabled=True, 92% >= 90% is excluded -> falls back to step 2 (claude-fable-5-2)
    choice_on = select_verifier(
        author,
        available,
        policy="cross_family",
        quota=quotas,
        quota_policy_enabled=True,
        quota_block_percent=90,
    )
    assert choice_on.fallback_step == 2
    assert choice_on.provider == "anthropic-cli"
    assert choice_on.model == "claude-fable-5-2"
    assert "quota" in choice_on.reason


def test_fallback_steps_2_3_and_4() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")

    # Step 2: no other family available, but same family higher/equal different model exists
    step2 = select_verifier(
        author,
        [
            AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="anthropic-cli", model="claude-fable-5-2"),
        ],
        policy="cross_family",
    )
    assert step2.fallback_step == 2
    assert step2.provider == "anthropic-cli"
    assert step2.model == "claude-fable-5-2"
    assert step2.effort == "high"

    # Step 3: only author's own model available -> same model, fresh context, clamped high effort
    step3 = select_verifier(
        author,
        [AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5")],
        policy="cross_family",
    )
    assert step3.fallback_step == 3
    assert step3.provider == "anthropic-cli"
    assert step3.model == "claude-opus-5-5"
    assert step3.effort == "high"

    # Step 4: all subscriptions blocked/rejected -> skip independent verifier and warn
    step4 = select_verifier(
        author,
        [
            AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        policy="cross_family",
        quota={
            "anthropic-cli": _quota(100.0, rejected=True),
            "openai-codex": _quota(100.0, rejected=True),
        },
    )
    assert step4.fallback_step == 4
    assert step4.provider is None
    assert step4.model is None
    assert step4.effort is None
    assert step4.available is False
    assert verifier_model_selected_payload(step4)["chosen"] is None
    assert verifier_model_selected_payload(step4)["fallback_step"] == 4


def test_same_model_policy_and_fallback_when_author_blocked() -> None:
    author = AuthorModel(provider="google-antigravity", model="gemini-4-argon-medium", effort="medium")
    available = [
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-argon-medium"),
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
    ]

    # Available: keeps author provider+model, clamps requested effort ("high" -> fixed "medium")
    choice = select_verifier(author, available, policy="same_model", requested_effort="high")
    assert choice.provider == "google-antigravity"
    assert choice.model == "gemini-4-argon-medium"
    assert choice.effort == "medium"
    assert choice.effort_adjustment is not None
    assert choice.policy == "same_model"
    assert choice.fallback_step == 1

    # Author subscription rejected -> falls back to chain (step 1: openai-codex/gpt-6.1-sol)
    fallback = select_verifier(
        author,
        available,
        policy="same_model",
        quota={"google-antigravity": _quota(None, rejected=True)},
    )
    assert fallback.provider == "openai-codex"
    assert fallback.model == "gpt-6.1-sol"
    assert fallback.policy == "same_model"
    assert fallback.fallback_step == 1
    assert "same_model" in fallback.reason


def test_explicit_policy_honors_target_and_falls_back_when_unavailable() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")
    available = [
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-argon-high"),
    ]

    # Explicit target available with custom effort
    choice = select_verifier(
        author,
        available,
        policy="explicit",
        explicit={"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "xhigh"},
    )
    assert choice.provider == "openai-codex"
    assert choice.model == "gpt-6.1-sol"
    assert choice.effort == "xhigh"
    assert choice.policy == "explicit"
    assert choice.fallback_step == 1

    # Explicit target rejected by quota -> falls back to step 1 (google-antigravity/gemini-4-argon-high)
    fallback_step1 = select_verifier(
        author,
        available,
        policy="explicit",
        explicit={"provider": "openai-codex", "model": "gpt-6.1-sol"},
        quota={"openai-codex": _quota(100.0, rejected=True)},
    )
    assert fallback_step1.provider == "google-antigravity"
    assert fallback_step1.model == "gemini-4-argon-high"
    assert fallback_step1.policy == "explicit"
    assert fallback_step1.fallback_step == 1
    assert "explicit" in fallback_step1.reason

    # Explicit target missing and no other family -> falls back to step 3 (same model)
    fallback_step3 = select_verifier(
        author,
        [AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5")],
        policy="explicit",
        explicit={"provider": "openai-codex", "model": "gpt-6.1-sol"},
    )
    assert fallback_step3.provider == "anthropic-cli"
    assert fallback_step3.model == "claude-opus-5-5"
    assert fallback_step3.fallback_step == 3
    assert "explicit" in fallback_step3.reason


def test_editable_tiers_change_cross_family_eligibility() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")  # tier 4
    available = [
        AvailableVerifierModel(provider="anthropic-cli", model="claude-opus-5-5"),
        AvailableVerifierModel(provider="openrouter", model="acme/deep-reviewer"),
    ]

    # Without override, acme/deep-reviewer has tier 0 -> skipped, falls back to step 3
    default_choice = select_verifier(author, available, policy="cross_family")
    assert default_choice.fallback_step == 3
    assert default_choice.model == "claude-opus-5-5"

    # With editable tiers promoting acme/deep-reviewer to tier 4 in family "acme" -> picked at step 1
    override_choice = select_verifier(
        author,
        available,
        policy="cross_family",
        tiers={"acme/deep-reviewer": {"family": "acme", "tier": 4}},
    )
    assert override_choice.fallback_step == 1
    assert override_choice.provider == "openrouter"
    assert override_choice.model == "acme/deep-reviewer"


def test_invalid_policy_raises_value_error() -> None:
    author = AuthorModel(provider="anthropic-cli", model="claude-opus-5-5")
    with pytest.raises(ValueError, match="Política"):
        select_verifier(author, [], policy="unknown_policy")
