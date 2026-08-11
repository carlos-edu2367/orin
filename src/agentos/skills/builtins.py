"""Discovery entry point for source-controlled built-in SKILL.md packages."""
from __future__ import annotations

from pathlib import Path

from .models import Skill, SkillScope, SkillSource
from .parser import parse_skill_file


def builtin_root() -> Path:
    return Path(__file__).with_name("builtin")


def load_builtin_skills(*, include_instructions: bool = False) -> tuple[Skill, ...]:
    """Read only metadata by default; registry.load performs the lazy body read."""
    root = builtin_root()
    if not root.exists():
        return ()
    return tuple(
        parse_skill_file(path, include_instructions=include_instructions, source=SkillSource.BUILTIN, scope=SkillScope.SYSTEM)
        for path in sorted(root.glob("*/SKILL.md"))
    )


__all__ = ["builtin_root", "load_builtin_skills"]
