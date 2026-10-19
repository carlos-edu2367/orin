"""W4: Skill passes its arguments to the instructions."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


@dataclass
class _Ref:
    id: str
    version: str = "1.0.0"


@dataclass
class _Skill:
    name: str


@dataclass
class _Loaded:
    ref: _Ref
    skill: _Skill
    instructions: str
    digest: str = "d"
    dependencies: tuple = field(default_factory=tuple)


class _Skills:
    def __init__(self, instructions: str) -> None:
        self.instructions = instructions

    def load(self, skill_id: str, version=None, available_tools=()):
        return _Loaded(_Ref(skill_id), _Skill(skill_id.title()), self.instructions)


def _toolset(tmp_path: Path, instructions: str) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), skills=_Skills(instructions))


def test_arguments_replace_the_placeholder(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path, "Review the file $ARGUMENTS carefully.").invoke("Skill", {"skill": "review", "args": "src/app.py"})
    assert "Review the file src/app.py carefully." in outcome.content and "$ARGUMENTS" not in outcome.content


def test_arguments_without_a_placeholder_are_appended(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path, "Do the review.").invoke("Skill", {"skill": "review", "args": "focus on tests"})
    assert "<skill-arguments>\nfocus on tests\n</skill-arguments>" in outcome.content


def test_a_loaded_skill_still_gets_new_arguments(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path, "Do the review.")
    toolset.invoke("Skill", {"skill": "review", "args": "first"})
    again = toolset.invoke("Skill", {"skill": "review", "args": "second"})
    assert "already loaded" in again.content and "second" in again.content
