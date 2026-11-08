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


def test_resolve_unqualified_skill_prefers_enabled_and_strips_leading_slash(tmp_path) -> None:
    from dataclasses import replace
    from agentos.skills.disk import DiskSkill, DiskSkillCatalog

    s_enabled = _skill("superpowers:brainstorming")
    s_disabled = replace(_skill("legacy:brainstorming"), enabled=False)
    registry = SkillRegistry([s_enabled, s_disabled])
    resolved = registry.resolve("/Brainstorming")
    assert resolved.id == "superpowers:brainstorming"

    catalog = DiskSkillCatalog(
        skills={
            "superpowers:brainstorming": DiskSkill(
                name="superpowers:brainstorming",
                description="Brainstorm",
                body="# Body",
                path=tmp_path / "SKILL.md",
                origin="user",
                kind="skill",
            )
        }
    )
    assert catalog.get("brainstorming") is not None
    assert catalog.get("/brainstorming").name == "superpowers:brainstorming"

