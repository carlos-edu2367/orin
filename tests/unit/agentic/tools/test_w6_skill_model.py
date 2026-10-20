"""W6 (W5 deviation 4): a forked skill's `model` picks its subagent's model, when the session allows it."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.tools._shared import ToolOutcome

from tests.unit.agentic.tools.test_disk_skill_tool import _skill, _toolset


def _fork(tmp_path: Path, model_line: str, resolver):
    _skill("research", f"---\ndescription: Research\ncontext: fork\nagent: Explore\n{model_line}---\nFind the config.")
    calls: list[dict] = []

    def run_subagent(subagent_type, task, **kwargs):
        calls.append(kwargs)
        return ToolOutcome("succeeded", "done", "found", {})

    toolset = _toolset(tmp_path, run_subagent=run_subagent)
    if resolver is not None:
        toolset._skill_model_for = resolver
    assert toolset.invoke("Skill", {"skill": "research"}).status == "succeeded"
    return calls[-1]


def test_an_allowed_model_reaches_the_subagent(tmp_path: Path) -> None:
    asked: list[str] = []
    call = _fork(tmp_path, "model: sonnet\n", lambda name: asked.append(name) or "fast-model")
    assert asked == ["sonnet"] and call.get("model_id") == "fast-model"


def test_a_model_the_session_refuses_runs_on_the_parents(tmp_path: Path) -> None:
    assert _fork(tmp_path, "model: sonnet\n", lambda name: None).get("model_id") is None


def test_without_a_model_nothing_is_asked(tmp_path: Path) -> None:
    asked: list[str] = []
    assert _fork(tmp_path, "", lambda name: asked.append(name)).get("model_id") is None
    assert asked == []


def test_the_session_resolves_through_aliases_and_the_allow_list(tmp_path: Path, monkeypatch) -> None:
    from tests.unit.cli_code.test_cli_turn_session import _session

    session = _session(tmp_path)
    session.effective_settings = type("S", (), {"layers": (type("L", (), {"name": "user", "data": {"modelAliases": {"sonnet": "m2"}}})(),)})()
    monkeypatch.setattr(session, "_resolve_child_model", lambda requested: requested)
    monkeypatch.setattr(session, "_child_model_allowed", lambda model, allow_current=False: model == "m2")
    assert session._allowed_requested_model("sonnet", "a skill") == "m2"
    assert session._allowed_requested_model("other", "a skill") is None
    assert session._allowed_requested_model("inherit", "a skill") is None
