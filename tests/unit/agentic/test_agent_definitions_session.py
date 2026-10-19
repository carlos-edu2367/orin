"""W5: the Agent tool starts agents defined on disk — their tools, mode, turns, model and instructions apply."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.agent_definitions import AgentToolPolicy, load_agent_definitions
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.tools._shared import ToolOutcome
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.paths import orin_user_dir
from agentos.skills.disk import load_disk_skills
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore, _text_batch


def _agent(text: str, file: str = "agent.md") -> None:
    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file).write_bytes(text.encode("utf-8"))


class _ChildProvider:
    def stream(self, _request):
        return iter(_text_batch("Reviewed: no issues."))


@pytest.fixture()
def session_and_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'agents.db').as_posix()}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    session = TurnSession(
        turn=dict(TURN), store=_RecordingStore(), agents_store=agents_store, memory_store=None,
        provider_factory=_ChildProvider, workspace_root=tmp_path, child_provider_factory=lambda _model: _ChildProvider(),
    )
    builds: list[dict] = []
    prompts: list[str] = []
    limits: list[dict] = []
    original_toolset, original_prompt, original_limits = TurnSession._toolset, TurnSession._subagent_prompt, TurnSession._subagent_limits

    def recording_toolset(self, **kwargs):
        toolset = original_toolset(self, **kwargs)
        builds.append({**kwargs, "toolset": toolset})
        return toolset

    def recording_prompt(self, record, task, toolset):
        prompt = original_prompt(self, record, task, toolset)
        prompts.append(prompt)
        return prompt

    def recording_limits(self, **kwargs):
        limits.append(kwargs)
        return original_limits(self, **kwargs)

    monkeypatch.setattr(TurnSession, "_toolset", recording_toolset)
    monkeypatch.setattr(TurnSession, "_subagent_prompt", recording_prompt)
    monkeypatch.setattr(TurnSession, "_subagent_limits", recording_limits)
    yield session, builds, prompts, limits
    engine.dispose()


REVIEWER = """---
name: code-reviewer
description: Reviews a change for bugs
tools: Read, Grep, Glob, Edit
disallowedTools: Edit
permissionMode: bypassPermissions
maxTurns: 3
skills: [checklist]
omitClaudeMd: true
---
REVIEWER-INSTRUCTIONS: report findings as path:line.
"""


def test_a_custom_agent_runs_with_its_own_tools_mode_turns_and_instructions(session_and_builds) -> None:
    session, builds, prompts, limits = session_and_builds
    _agent(REVIEWER)
    skill = orin_user_dir() / "skills" / "checklist"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"---\ndescription: Review checklist\n---\nCHECKLIST-BODY: look for missing tests.")
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    session._disk_skills = load_disk_skills(workspace_root=None, trusted=False)
    outcome = session._run_subagent(subagent_type="code-reviewer", task="Review the change", description="review")
    assert outcome.status == "succeeded", outcome.content
    child = [build for build in builds if build.get("subagents") is False][-1]
    published = {item.name for item in child["toolset"].definitions()}
    assert "read_file" in published and "search_files" in published and "glob_files" in published
    assert "edit_file" not in published and "write_file" not in published and "run_command" not in published
    assert isinstance(child["toolset"]._policy, AgentToolPolicy)
    assert session.permission_mode == "accept_edits" and child["permission_mode"] == "accept_edits"  # bypass never escalates
    assert limits[-1]["max_iterations"] == 3
    assert "REVIEWER-INSTRUCTIONS" in prompts[-1] and "CHECKLIST-BODY" in prompts[-1]
    assert "## Workspace instructions" not in prompts[-1]


def test_the_verifier_builtin_cannot_edit(session_and_builds) -> None:
    session, builds, _prompts, _limits = session_and_builds
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    outcome = session._run_subagent(subagent_type="verifier", task="Check the tests pass")
    assert outcome.status == "succeeded", outcome.content
    published = {item.name for item in [b for b in builds if b.get("subagents") is False][-1]["toolset"].definitions()}
    assert "read_file" in published and not {"edit_file", "write_file", "notebook_edit"} & published


def test_an_unknown_type_lists_the_agents_that_exist(session_and_builds) -> None:
    session, _builds, _prompts, _limits = session_and_builds
    _agent(REVIEWER)
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    outcome = session._run_subagent(subagent_type="nobody", task="x")
    assert outcome.status == "failed" and "code-reviewer" in outcome.content and "verifier" in outcome.content


def test_a_definition_model_resolves_through_model_aliases(session_and_builds, monkeypatch: pytest.MonkeyPatch) -> None:
    session, _builds, _prompts, _limits = session_and_builds
    _agent("---\nname: quick\ndescription: Quick\nmodel: haiku\n---\nx")
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    session.effective_settings = type("S", (), {"layers": (type("L", (), {"name": "user", "data": {"modelAliases": {"haiku": "fast-model"}}})(),)})()
    monkeypatch.setattr(session, "_resolve_child_model", lambda requested: requested if requested == "fast-model" else None)
    monkeypatch.setattr(session, "_child_model_allowed", lambda model, allow_current=False: True)
    assert session._definition_model(session._subagent_spec("quick")) == "fast-model"
    monkeypatch.setattr(session, "_child_model_allowed", lambda model, allow_current=False: False)
    assert session._definition_model(session._subagent_spec("quick")) is None  # not a model this person allows


def test_the_agent_tool_hands_a_custom_name_to_the_session(tmp_path: Path) -> None:
    seen: list[str] = []

    def run_subagent(subagent_type, task, **kwargs):
        seen.append(subagent_type)
        return ToolOutcome("succeeded", "ok", "done", {})

    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), run_subagent=run_subagent, public_tool_names=True)
    assert toolset.invoke("Agent", {"subagent_type": "code-reviewer", "prompt": "Review", "description": "r"}).status == "succeeded"
    assert seen == ["code-reviewer"]


def test_custom_agents_are_listed_for_the_model(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    _agent(REVIEWER)
    session = _session(tmp_path, "hi")
    session.build_runtime()
    texts = [item.text for item in session._reminders.drain() if item.kind == "agents"]
    assert texts and "code-reviewer" in texts[0] and "verifier" in texts[0]
