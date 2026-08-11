from __future__ import annotations

from agentos.skills.models import Skill, SkillScope, SkillSource
from agentos.skills.registry import SkillRegistry
from agentos.skills.retrieval import RetrievalQuery


def skill(skill_id: str, *, description: str, tags: tuple[str, ...]) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        version="1.0.0",
        description=description,
        tags=tags,
        instructions="# Workflow",
        scope=SkillScope.SYSTEM,
        source=SkillSource.BUILTIN,
    )


def test_pdf_attachment_outranks_unrelated_skills() -> None:
    registry = SkillRegistry([
        skill("pdf", description="Read, compare, create, and modify PDF files.", tags=("pdf", "documents")),
        skill("frontend", description="Build responsive web interfaces.", tags=("react", "css")),
    ])

    result = registry.retrieve(RetrievalQuery("compare this report", attachments=("report.pdf",)))

    assert result.items[0].id == "pdf"
    assert not hasattr(result.items[0], "instructions")


def test_retrieval_excludes_skills_that_cannot_run_with_available_tools() -> None:
    registry = SkillRegistry([
        skill("pdf", description="Read PDF files.", tags=("pdf",)),
        Skill(
            id="browser",
            name="Browser",
            version="1.0.0",
            description="Automate browser pages.",
            tags=("browser",),
            instructions="# Browser workflow",
            requires_tools=("browser.open",),
            scope=SkillScope.SYSTEM,
            source=SkillSource.BUILTIN,
        ),
    ])

    result = registry.retrieve(RetrievalQuery("automate browser", available_tools=()))

    assert [item.id for item in result.items] == []
