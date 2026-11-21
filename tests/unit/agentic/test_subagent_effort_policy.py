from __future__ import annotations

import json

import pytest

from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.settings.paths import orin_user_dir

from tests.unit.agentic.test_agent_definitions_session import session_and_builds  # noqa: F401 - fixture


def _write_user_settings(data: dict) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def seen_efforts(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    seen: list[str | None] = []
    original = AgenticTurnRuntime.run

    def run(self, turn_id, **kwargs):
        seen.append(self.effort)
        return original(self, turn_id, **kwargs)

    monkeypatch.setattr(AgenticTurnRuntime, "run", run)
    return seen


def test_agent_call_effort_parameter_reaches_subagent(session_and_builds, seen_efforts) -> None:  # noqa: F811
    session, _builds, _prompts, _limits = session_and_builds
    session.turn["provider"], session.turn["model_id"] = "anthropic", "claude-opus-5-5"
    outcome = session._run_subagent(subagent_type="explore", task="Search files", effort="low")
    assert outcome.status == "succeeded", outcome.content
    assert seen_efforts[-1] == "low"


def test_model_default_policy_runs_child_without_effort_even_when_parent_has_effort(
    session_and_builds, seen_efforts
) -> None:  # noqa: F811
    _write_user_settings({"effortLevel": "high", "effort": {"subagentPolicy": "model_default"}})
    session, _builds, _prompts, _limits = session_and_builds
    session.turn["provider"], session.turn["model_id"] = "anthropic", "claude-opus-5-5"
    session.turn["effort"] = "max"
    session.build_runtime()
    outcome = session._run_subagent(subagent_type="explore", task="Search files")
    assert outcome.status == "succeeded", outcome.content
    assert seen_efforts[-1] is None


def test_inherit_policy_passes_parent_effort_clamped_to_child_model(
    session_and_builds, seen_efforts
) -> None:  # noqa: F811
    _write_user_settings({"effort": {"subagentPolicy": "inherit"}})
    session, _builds, _prompts, _limits = session_and_builds
    session.turn["provider"], session.turn["model_id"] = "anthropic", "claude-opus-5-5"
    session._child_model_authorizer = lambda m: m in {"claude-opus-5-5", "claude-opus-4-6"}
    session.turn["effort"] = "xhigh"
    session.build_runtime()
    # Child runs on claude-opus-4-6 which does not support xhigh -> clamped to high
    outcome = session._run_subagent(
        subagent_type="explore",
        task="Inspect code",
        model_id="claude-opus-4-6",
    )
    assert outcome.status == "succeeded", outcome.content
    assert seen_efforts[-1] == "high"


def test_by_type_policy_applies_plan_high_and_explore_low(
    session_and_builds, seen_efforts
) -> None:  # noqa: F811
    _write_user_settings(
        {
            "effort": {
                "subagentPolicy": "by_type",
                "byType": {
                    "explore": "low",
                    "plan": "high",
                    "general": "inherit",
                    "fork": "inherit",
                },
            }
        }
    )
    session, _builds, _prompts, _limits = session_and_builds
    session.turn["provider"], session.turn["model_id"] = "anthropic", "claude-opus-5-5"
    session.turn["effort"] = "medium"
    session.build_runtime()

    o_plan = session._run_subagent(subagent_type="plan", task="Plan architecture")
    assert o_plan.status == "succeeded", o_plan.content
    assert seen_efforts[-1] == "high"

    o_explore = session._run_subagent(subagent_type="explore", task="Search repo")
    assert o_explore.status == "succeeded", o_explore.content
    assert seen_efforts[-1] == "low"

    o_general = session._run_subagent(subagent_type="general", task="Implement feature")
    assert o_general.status == "succeeded", o_general.content
    assert seen_efforts[-1] == "medium"


def test_agent_tool_accepts_effort_in_input_model_and_handler() -> None:
    from agentos.agentic.tools.agents.agent import AgentInput, AgentTools

    parsed = AgentInput(description="Find files", prompt="Search src", subagent_type="Explore", effort="low")
    assert parsed.effort == "low"

    captured: list[dict] = []

    class _FakeToolset(AgentTools):
        def __init__(self) -> None:
            self._delegate = None
            self._run_subagent = lambda *args, **kwargs: captured.append({"args": args, "kwargs": kwargs})

    toolset = _FakeToolset()
    toolset.run_subagent(subagent_type="explore", task="Find files", effort="low")
    assert captured[-1]["kwargs"]["effort"] == "low"
