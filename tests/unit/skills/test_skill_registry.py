from __future__ import annotations

import pytest

from agentos.skills.models import Skill
from agentos.skills.registry import SkillNotFound, SkillRegistry


def _skill(skill_id: str, *, version: str = "1.0.0", name: str = "Brainstorming") -> Skill:
    return Skill(
        id=skill_id,
        name=name,
        version=version,
        description="Explore user intent and design before implementation.",
        instructions="# Brainstorming\nSteps here.",
    )


def test_resolve_unqualified_skill_name_when_unique_namespace_match_exists() -> None:
    registry = SkillRegistry([_skill("superpowers:brainstorming")])
    resolved = registry.resolve("brainstorming")
    assert resolved.id == "superpowers:brainstorming"


def test_resolve_unqualified_skill_name_fails_with_candidate_ids_when_ambiguous() -> None:
    registry = SkillRegistry(
        [
            _skill("superpowers:brainstorming"),
            _skill("custom:brainstorming"),
        ]
    )
    with pytest.raises(SkillNotFound) as exc_info:
        registry.resolve("brainstorming")
    message = str(exc_info.value)
    assert "superpowers:brainstorming" in message
    assert "custom:brainstorming" in message
