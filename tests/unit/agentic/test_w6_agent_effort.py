"""W6 (W5 deviation 9): an agent definition's `effort` sets its subagent's reasoning effort, when the model takes one."""
from __future__ import annotations

import pytest

from agentos.agentic.agent_definitions import load_agent_definitions
from agentos.agentic.runtime import AgenticTurnRuntime

from tests.unit.agentic.test_agent_definitions_session import _agent, session_and_builds  # noqa: F401 - fixture


@pytest.fixture()
def efforts(monkeypatch: pytest.MonkeyPatch) -> list:
    seen: list = []
    original = AgenticTurnRuntime.run

    def run(self, turn_id, **kwargs):
        seen.append(self.effort)
        return original(self, turn_id, **kwargs)

    monkeypatch.setattr(AgenticTurnRuntime, "run", run)
    return seen


def test_the_definitions_effort_reaches_the_subagent(session_and_builds, efforts) -> None:  # noqa: F811
    session, _builds, _prompts, _limits = session_and_builds
    session.turn["provider"], session.turn["model_id"] = "openai", "gpt-5"
    _agent("---\nname: deep\ndescription: Deep thinker\neffort: high\n---\nThink hard.")
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    outcome = session._run_subagent(subagent_type="deep", task="Think")
    assert outcome.status == "succeeded", outcome.content
    assert efforts[-1] == "high"


def test_a_model_without_reasoning_control_gets_no_effort(session_and_builds, efforts) -> None:  # noqa: F811
    session, _builds, _prompts, _limits = session_and_builds  # litellm: no reasoning control
    _agent("---\nname: deep\ndescription: Deep thinker\neffort: high\n---\nThink hard.")
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    assert session._run_subagent(subagent_type="deep", task="Think").status == "succeeded"
    assert efforts[-1] is None


def test_an_unknown_effort_is_ignored(session_and_builds, efforts) -> None:  # noqa: F811
    session, _builds, _prompts, _limits = session_and_builds
    session.turn["provider"], session.turn["model_id"] = "openai", "gpt-5"
    _agent("---\nname: deep\ndescription: Deep thinker\neffort: enormous\n---\nThink hard.")
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    assert session._run_subagent(subagent_type="deep", task="Think").status == "succeeded"
    assert efforts[-1] is None
