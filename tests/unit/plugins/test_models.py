import pytest
from agentos.plugins.models import CommandContribution, PluginInspection, PluginRef, PluginState, SkillContribution, plugin_id_from_name

def test_models_and_transitions():
    assert plugin_id_from_name("My Cool Plugin!") == "my-cool-plugin"
    with pytest.raises(ValueError): plugin_id_from_name("###")
    assert str(PluginRef("demo", "1.0.0")) == "demo@1.0.0"
    inspection = PluginInspection(PluginRef("demo", "1.0.0"), "Demo", "", "", None, "x", skills=(SkillContribution("s", "S", "d", "x"),))
    assert inspection.contribution_count == 1 and inspection.requires_approval
    assert PluginState.PENDING_APPROVAL.can_transition_to(PluginState.ACTIVE)
    assert not PluginState.INSPECTED.can_transition_to(PluginState.ACTIVE)


def _inspection(**kwargs):
    return PluginInspection(PluginRef("demo", "1.0.0"), "Demo", "", "", None, "abc", **kwargs)


def test_a_commands_only_package_is_installable():
    inspection = _inspection(commands=(
        CommandContribution("demo:daily", "daily", "Daily note", "[date]", "commands/daily.md"),
    ))

    assert inspection.contribution_count == 1
    assert inspection.is_installable
    assert inspection.requires_approval


def test_a_package_with_nothing_at_all_is_still_not_installable():
    assert not _inspection().is_installable
