from __future__ import annotations

from datetime import UTC, datetime
import math
from pathlib import Path

from sqlalchemy import create_engine

from agentos.agentic.context_overflow import parse_overflow_limit
from agentos.persistence.postgres.context_observations import ContextObservationStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.providers.claude_cli.effective_window import (
    EffectiveWindow,
    effective_window_for,
)
from agentos.quota.claude_account import ClaudeAccountReader

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_case1_no_credits_clamps_overage_fable_keeps_opus_and_sonnet_learns() -> None:
    state = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json").read()
    assert state.extra_usage_enabled is False
    assert "Fable 5.2" in state.overage_models

    fable = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=state,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert isinstance(fable, EffectiveWindow)
    assert fable.tokens == 200_000
    assert fable.source == "account_credits"
    assert fable.rule_index == 3
    assert fable.cli_model_arg is None
    assert "200 mil" in fable.detail

    opus = effective_window_for(
        "anthropic",
        "claude-opus-5-5",
        account_state=state,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=1_000_000,
    )
    assert opus.tokens == 1_000_000
    assert opus.source == "cli_catalog"
    assert opus.rule_index == 4

    sonnet_before = effective_window_for(
        "anthropic",
        "claude-sonnet-5-5",
        account_state=state,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert sonnet_before.tokens == 1_000_000
    assert sonnet_before.source == "cli_catalog"
    assert sonnet_before.rule_index == 4

    sonnet_after = effective_window_for(
        "anthropic",
        "claude-sonnet-5-5",
        learned_limit=200_000,
        learned_source="error_raw",
        account_state=state,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert sonnet_after.tokens == 200_000
    assert sonnet_after.source == "learned"
    assert sonnet_after.rule_index == 2


def test_case2_credits_on_allows_1m_and_opt_in_appends_1m_suffix() -> None:
    state = ClaudeAccountReader(FIXTURES / "claude-json-credits-on.json").read()
    assert state.extra_usage_enabled is True

    # Default longContextWithCredits=False: does not clamp to 200k and does NOT append [1m]
    fable_default = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=state,
        long_context_with_credits=False,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert fable_default.tokens == 1_000_000
    assert fable_default.cli_model_arg is None

    # Opt-in longContextWithCredits=True with credits ON: appends [1m] and uses 1M
    fable_opt_in = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=state,
        long_context_with_credits=True,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert fable_opt_in.tokens == 1_000_000
    assert fable_opt_in.source == "credits_1m"
    assert fable_opt_in.rule_index == 3
    assert fable_opt_in.cli_model_arg == "claude-fable-5-2[1m]"


def test_case3_missing_or_empty_claude_json_falls_back_to_catalog_without_error(tmp_path: Path) -> None:
    missing_state = ClaudeAccountReader(tmp_path / "does-not-exist.json").read()
    res = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=missing_state,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert res.tokens == 1_000_000
    assert res.source == "cli_catalog"
    assert res.rule_index == 4

    # Without cli_catalog_window -> rule 5 fixed catalog
    fixed = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=missing_state,
        cli_catalog_window=None,
        fixed_catalog_window=200_000,
    )
    assert fixed.tokens == 200_000
    assert fixed.source == "fixed_catalog"
    assert fixed.rule_index == 5

    # Without any catalog -> rule 6 fallback 200k
    fallback = effective_window_for(
        "anthropic",
        "claude-unknown-9",
        account_state=None,
        cli_catalog_window=None,
        fixed_catalog_window=None,
    )
    assert fallback.tokens == 200_000
    assert fallback.source == "fallback"
    assert fallback.rule_index == 6


def test_case4_precedence_override_beats_all_and_learned_beats_credits_and_catalog() -> None:
    no_credits = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json").read()

    # Override beats learned, credits, and catalog
    overridden = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides={"anthropic/claude-fable-5-2": 300_000},
        learned_limit=180_000,
        learned_source="estimate",
        account_state=no_credits,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert overridden.tokens == 300_000
    assert overridden.source == "override"
    assert overridden.rule_index == 1

    # Out-of-range overrides are ignored (must be 50_000..1_000_000)
    out_of_range = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides={"anthropic/claude-fable-5-2": 10_000},
        learned_limit=180_000,
        learned_source="estimate",
        account_state=no_credits,
        cli_catalog_window=1_000_000,
    )
    assert out_of_range.tokens == 180_000
    assert out_of_range.source == "learned"
    assert out_of_range.rule_index == 2

    # Learned beats account_credits and cli_catalog
    learned = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        learned_limit=250_000,
        learned_source="error_raw",
        account_state=no_credits,
        cli_catalog_window=1_000_000,
    )
    assert learned.tokens == 250_000
    assert learned.source == "learned"
    assert learned.rule_index == 2


def test_case5_overflow_error_raw_and_estimate_learning(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'learn.db').as_posix()}")
    upgrade(engine, "head")
    try:
        store = ContextObservationStore(engine, clock=lambda: datetime(2026, 11, 21, 10, 0, tzinfo=UTC))

        raw_limit = parse_overflow_limit("prompt is too long: 205123 tokens > 200000 maximum")
        assert raw_limit == 200_000
        obs_raw = store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            raw_limit,
            "error_raw",
            first_message_tokens=205_123,
        )
        assert obs_raw.observed_limit_tokens == 200_000
        assert obs_raw.source == "error_raw"

        # Without numeric limit in raw error -> floor(0.9 * N), source="estimate"
        assert parse_overflow_limit("Prompt is too long") is None
        n = 195_000
        est_limit = math.floor(0.9 * n)
        obs_est = store.learn(
            "u1",
            "anthropic",
            "claude-sonnet-5-5",
            est_limit,
            "estimate",
            first_message_tokens=n,
        )
        assert obs_est.observed_limit_tokens == 175_500
        assert obs_est.source == "estimate"
    finally:
        engine.dispose()


def test_case6_invalidation_on_credits_change_or_cli_version_change(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'inv.db').as_posix()}")
    upgrade(engine, "head")
    try:
        store = ContextObservationStore(engine, clock=lambda: datetime(2026, 11, 21, 10, 0, tzinfo=UTC))
        store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            200_000,
            "error_raw",
            cli_version="2.1.309",
            account_credits_on=False,
        )
        assert store.invalidate_if_environment_changed(
            "u1", "anthropic", "claude-fable-5-2", cli_version="2.1.309", account_credits_on=True
        )
        assert store.get("u1", "anthropic", "claude-fable-5-2") is None

        store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            200_000,
            "error_raw",
            cli_version="2.1.309",
            account_credits_on=False,
        )
        assert store.invalidate_if_environment_changed(
            "u1", "anthropic", "claude-fable-5-2", cli_version="2.1.310", account_credits_on=False
        )
        assert store.get("u1", "anthropic", "claude-fable-5-2") is None
    finally:
        engine.dispose()


def test_case7_long_context_with_credits_never_appends_1m_when_credits_disabled() -> None:
    no_credits = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json").read()
    res = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=no_credits,
        long_context_with_credits=True,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert res.tokens == 200_000
    assert res.source == "account_credits"
    assert res.cli_model_arg is None

    # Also never appends [1m] when account state is unknown (extra_usage_enabled is None)
    unknown_res = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        account_state=None,
        long_context_with_credits=True,
        cli_catalog_window=1_000_000,
        fixed_catalog_window=200_000,
    )
    assert unknown_res.cli_model_arg is None
