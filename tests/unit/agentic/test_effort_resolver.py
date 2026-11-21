from __future__ import annotations

from agentos.agentic.effort_levels import EffortAdjustment, ModelEffortInfo
from agentos.agentic.effort_resolver import (
    EffortContext,
    EffortEscalator,
    ResolvedEffort,
    resolve_effort,
)
from agentos.settings.loader import EffectiveSettings, SettingsLayer


def _effective(user: dict | None = None, project: dict | None = None) -> EffectiveSettings:
    layers: list[SettingsLayer] = []
    merged: dict = {}
    if user:
        layers.append(SettingsLayer(name="user", path=None, data=user))
        merged.update(user)
    if project:
        layers.append(SettingsLayer(name="project", path=None, data=project))
        merged.update(project)
    return EffectiveSettings(data=merged, layers=tuple(layers), provenance={}, errors=(), trusted=True)


def test_resolution_precedence_rules_1_to_9() -> None:
    settings = _effective(
        user={
            "effortLevel": "low",
            "effortByModel": {"anthropic/claude-opus-5-5": "medium"},
            "effort": {
                "planMode": "high",
                "subagentPolicy": "by_type",
                "byType": {"explore": "low", "plan": "high", "general": "inherit"},
                "boostKeyword": True,
            },
        }
    )

    # Rule 1: call_effort wins for subagent over definition, role, parent, global
    r1 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_subagent=True,
            subagent_type="plan",
            call_effort="xhigh",
            definition_effort="low",
            role_effort="medium",
            parent_effort="max",
            global_effort="low",
            settings=settings,
        )
    )
    assert r1 == ResolvedEffort(level="xhigh", source="call", adjustment=None)

    # Rule 2: boost steps up one level above turn_effort for main turn
    r2 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            boost=True,
            turn_effort="high",
            is_plan_mode=True,
            settings=settings,
        )
    )
    assert r2 == ResolvedEffort(level="xhigh", source="boost", adjustment=None)

    # Rule 3: turn_effort beats role (planMode), model_pref, global
    r3 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            turn_effort="max",
            is_plan_mode=True,
            settings=settings,
        )
    )
    assert r3 == ResolvedEffort(level="max", source="turn", adjustment=None)

    # Rule 4: definition_effort beats role (by_type) and parent for subagent
    r4 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_subagent=True,
            subagent_type="explore",
            definition_effort="max",
            parent_effort="medium",
            settings=settings,
        )
    )
    assert r4 == ResolvedEffort(level="max", source="definition", adjustment=None)

    # Rule 5: role effort (planMode on main turn, byType on subagent) beats model_pref and global
    r5_plan = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_plan_mode=True,
            settings=settings,
        )
    )
    assert r5_plan == ResolvedEffort(level="high", source="role", adjustment=None)

    r5_sub = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_subagent=True,
            subagent_type="plan",
            parent_effort="low",
            settings=settings,
        )
    )
    assert r5_sub == ResolvedEffort(level="high", source="role", adjustment=None)

    # Rule 6: parent_effort when subagentPolicy == inherit (or byType[general] == inherit)
    r6 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_subagent=True,
            subagent_type="general",
            parent_effort="xhigh",
            settings=settings,
        )
    )
    assert r6 == ResolvedEffort(level="xhigh", source="parent", adjustment=None)

    # Rule 7: effortByModel beats global effortLevel on main turn
    r7 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            settings=settings,
        )
    )
    assert r7 == ResolvedEffort(level="medium", source="model_pref", adjustment=None)

    # Rule 8: global effortLevel when no effortByModel matches
    r8 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-sonnet-5-5",
            settings=settings,
        )
    )
    assert r8 == ResolvedEffort(level="low", source="global", adjustment=None)

    # Rule 9: nothing set -> None with source "none"
    r9 = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
        )
    )
    assert r9 == ResolvedEffort(level=None, source="none", adjustment=None)


def test_ultrathink_keyword_only_applies_when_enabled() -> None:
    off = _effective(user={"effort": {"boostKeyword": False}})
    on = _effective(user={"effort": {"boostKeyword": True}})

    r_off = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            message="Please ultrathink about this bug",
            settings=off,
        )
    )
    assert r_off == ResolvedEffort(level=None, source="none", adjustment=None)

    # With boostKeyword enabled and no prior level, steps up 1 level from model default (medium -> high)
    r_on = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            message="Please ultrathink about this bug",
            settings=on,
        )
    )
    assert r_on == ResolvedEffort(level="high", source="boost", adjustment=None)


def test_subagent_max_cap_and_project_lower_only() -> None:
    # Project can lower effort.maxForSubagents from high -> medium
    settings = _effective(
        user={"effort": {"maxForSubagents": "high"}},
        project={"effort": {"maxForSubagents": "medium"}},
    )
    res = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_subagent=True,
            call_effort="max",
            settings=settings,
        )
    )
    assert res.level == "medium"
    assert res.source == "call"
    assert res.adjustment == EffortAdjustment(requested="max", applied="medium", reason="subagent_max")

    # Main turn is NOT capped by effort.maxForSubagents
    main_res = resolve_effort(
        EffortContext(
            provider="anthropic",
            model_id="claude-opus-5-5",
            is_subagent=False,
            turn_effort="max",
            settings=settings,
        )
    )
    assert main_res == ResolvedEffort(level="max", source="turn", adjustment=None)


def test_effort_escalator_steps_up_after_two_failures_and_proposes_model_switch_at_ceiling() -> None:
    escalator = EffortEscalator(threshold=2)
    on_settings = _effective(user={"effort": {"escalateOnFailure": True}})
    off_settings = _effective(user={"effort": {"escalateOnFailure": False}})

    # Disabled setting never escalates
    a_off = escalator.on_verifier_failed(
        "t-off",
        provider="anthropic",
        model_id="claude-opus-5-5",
        current_effort="medium",
        settings=off_settings,
    )
    assert a_off.escalated is False and a_off.propose_model_switch is False

    # Failure 1 -> below threshold
    a1 = escalator.on_verifier_failed(
        "t-1",
        provider="anthropic",
        model_id="claude-opus-5-5",
        current_effort="medium",
        settings=on_settings,
    )
    assert a1.failures == 1
    assert a1.escalated is False
    assert a1.next_effort is None

    # Failure 2 -> escalates 1 step (medium -> high)
    a2 = escalator.on_verifier_failed(
        "t-1",
        provider="anthropic",
        model_id="claude-opus-5-5",
        current_effort="medium",
        settings=on_settings,
    )
    assert a2.failures == 2
    assert a2.escalated is True
    assert a2.next_effort == "high"
    assert a2.propose_model_switch is False

    # Already at ceiling -> proposes model switch, does not switch autonomously
    info = ModelEffortInfo(levels=("low", "medium", "high"), default="medium", source="test")
    escalator.on_verifier_failed("t-ceil", current_effort="high", settings=on_settings, model_info=info)
    a_ceil = escalator.on_verifier_failed("t-ceil", current_effort="high", settings=on_settings, model_info=info)
    assert a_ceil.escalated is False
    assert a_ceil.propose_model_switch is True
