from agentos.skills.builtins import load_builtin_skills


def test_builtin_packages_expose_metadata_without_loading_instruction_bodies() -> None:
    skills = load_builtin_skills()

    assert {skill.id for skill in skills} == {
        "systematic-debugging", "testing", "code-review", "technical-research",
        "react-spa", "next-js-app-router", "python-fastapi-service", "frontend-accessibility",
    }
    assert all(skill.instructions is None for skill in skills)
    assert all(skill.package_path and skill.package_path.name == "SKILL.md" for skill in skills)


def test_builtin_core_instructions_load_lazily_when_requested() -> None:
    skills = {skill.id: skill for skill in load_builtin_skills(include_instructions=True)}

    assert "## Workflow" in (skills["systematic-debugging"].instructions or "")
