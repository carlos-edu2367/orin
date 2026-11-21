from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentos.quota.models import Confidence, QuotaStatus
from agentos.quota.policy import QuotaPolicy
from agentos.settings.loader import EffectiveSettings, SettingsLayer


class StubQuotaService:
    def __init__(self, statuses: dict[str, QuotaStatus]) -> None:
        self._statuses = statuses

    def status_for(self, user_id: str, provider: str, model_id: str | None = None) -> QuotaStatus:
        return self._statuses.get(
            provider,
            QuotaStatus(max_percent=None, rejected=False, confidence=Confidence.UNKNOWN, observed_at=None),
        )


def _settings(data: dict) -> EffectiveSettings:
    return EffectiveSettings(
        data=data,
        provenance={},
        layers=(SettingsLayer(name="user", path=None, data=data),),
        errors=(),
        trusted=True,
    )


def test_quota_policy_disabled_never_warns_blocks_or_switches():
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    svc = StubQuotaService(
        {
            "anthropic-cli": QuotaStatus(
                max_percent=99.0,
                rejected=True,
                confidence=Confidence.MEASURED,
                observed_at=now,
                resets_at=now + timedelta(hours=1),
            ),
            "openai-codex": QuotaStatus(
                max_percent=10.0,
                rejected=False,
                confidence=Confidence.MEASURED,
                observed_at=now,
            ),
        }
    )
    policy = QuotaPolicy(svc)
    settings = _settings(
        {
            "quota": {
                "policy": {"enabled": False, "warnPercent": 80, "blockBackgroundPercent": 90},
                "fallback": {"enabled": True, "chain": ["openai-codex/gpt-6-luna"]},
            }
        }
    )

    assert policy.warn("u1", "anthropic-cli", settings=settings).should_warn is False
    assert policy.check_background("u1", "anthropic-cli", settings=settings).allowed is True
    assert (
        policy.select_fallback(
            "u1",
            "anthropic-cli",
            "claude-sonnet-5-5",
            settings=settings,
        )
        is None
    )


def test_quota_policy_warn_at_85_and_block_background_at_92():
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    settings = _settings(
        {
            "quota": {
                "policy": {"enabled": True, "warnPercent": 80, "blockBackgroundPercent": 90},
            }
        }
    )

    svc_85 = StubQuotaService(
        {
            "anthropic-cli": QuotaStatus(
                max_percent=85.0,
                rejected=False,
                confidence=Confidence.MEASURED,
                observed_at=now,
            )
        }
    )
    pol_85 = QuotaPolicy(svc_85)
    warn_res = pol_85.warn("u1", "anthropic-cli", settings=settings)
    assert warn_res.should_warn is True
    assert warn_res.used_percent == 85.0
    assert warn_res.threshold == 80
    assert pol_85.check_background("u1", "anthropic-cli", settings=settings).allowed is True

    svc_92 = StubQuotaService(
        {
            "anthropic-cli": QuotaStatus(
                max_percent=92.0,
                rejected=False,
                confidence=Confidence.MEASURED,
                observed_at=now,
            )
        }
    )
    pol_92 = QuotaPolicy(svc_92)
    bg_res = pol_92.check_background("u1", "anthropic-cli", settings=settings)
    assert bg_res.allowed is False
    assert bg_res.used_percent == 92.0
    assert bg_res.threshold == 90


def test_quota_policy_fallback_only_between_turns_never_with_pending_tool_effects():
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    svc = StubQuotaService(
        {
            "anthropic-cli": QuotaStatus(
                max_percent=100.0,
                rejected=True,
                confidence=Confidence.MEASURED,
                observed_at=now,
                resets_at=now + timedelta(hours=2),
            ),
            "openai-codex": QuotaStatus(
                max_percent=25.0,
                rejected=False,
                confidence=Confidence.MEASURED,
                observed_at=now,
            ),
        }
    )
    policy = QuotaPolicy(svc)
    settings = _settings(
        {
            "quota": {
                "policy": {"enabled": True},
                "fallback": {
                    "enabled": True,
                    "chain": [
                        "anthropic-cli/claude-sonnet-5-5",
                        "openai-codex/gpt-6-luna",
                    ],
                },
            }
        }
    )

    # Never switch mid-turn when tool effects are pending
    blocked = policy.select_fallback(
        "u1",
        "anthropic-cli",
        "claude-sonnet-5-5",
        settings=settings,
        has_pending_tool_effects=True,
    )
    assert blocked is None

    # Between turns (no pending tool effects), switches to next non-rejected entry in chain
    emitted: list[tuple[str, dict]] = []
    decision = policy.select_fallback(
        "u1",
        "anthropic-cli",
        "claude-sonnet-5-5",
        settings=settings,
        has_pending_tool_effects=False,
        emit_event=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    assert decision is not None
    assert decision.from_provider == "anthropic-cli"
    assert decision.from_model == "claude-sonnet-5-5"
    assert decision.to_provider == "openai-codex"
    assert decision.to_model == "gpt-6-luna"
    assert decision.reason == "quota_rejected"
    assert len(emitted) == 1
    assert emitted[0][0] == "quota.fallback_applied"
    assert emitted[0][1]["to_provider"] == "openai-codex"
    assert emitted[0][1]["to_model"] == "gpt-6-luna"
