from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentos.skills.models import Skill, SkillDependencies, SkillScope, SkillSource
from agentos.skills.parser import SkillParseError, parse_skill_file, parse_skill_markdown
from agentos.skills.registry import (
    SkillDependencyCycle,
    SkillDependencyDepthExceeded,
    SkillRegistry,
    SkillUnavailable,
)
from agentos.skills.retrieval import RetrievalQuery


def skill(
    skill_id: str,
    *,
    version: str = "1.0.0",
    dependencies: tuple[str, ...] = (),
    requires_tools: tuple[str, ...] = (),
) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        version=version,
        description=f"Procedure for {skill_id}.",
        instructions=f"# {skill_id}\n\nFollow the procedure.",
        dependencies=SkillDependencies(skills=dependencies, tools=requires_tools),
        scope=SkillScope.SYSTEM,
        source=SkillSource.BUILTIN,
    )


def test_resolve_returns_an_immutable_published_version() -> None:
    registry = SkillRegistry([skill("pdf", version="1.0.0"), skill("pdf", version="1.2.0")])

    resolved = registry.resolve("pdf")

    assert resolved.ref.version == "1.2.0"
    with pytest.raises(FrozenInstanceError):
        resolved.description = "changed"  # type: ignore[misc]


def test_load_detects_cycles_before_returning_any_instructions() -> None:
    registry = SkillRegistry([
        skill("pdf", dependencies=("filesystem",)),
        skill("filesystem", dependencies=("pdf",)),
    ])

    with pytest.raises(SkillDependencyCycle, match="pdf -> filesystem -> pdf"):
        registry.load("pdf")


def test_load_refuses_skills_with_unavailable_required_tools() -> None:
    registry = SkillRegistry([skill("pdf", requires_tools=("filesystem.read",))])

    with pytest.raises(SkillUnavailable, match="filesystem.read"):
        registry.load("pdf", available_tools=())


def test_read_instructions_allows_detail_access_for_an_unavailable_skill() -> None:
    registry = SkillRegistry([skill("pdf", requires_tools=("filesystem.read",))])

    assert registry.read_instructions("pdf") == "# pdf\n\nFollow the procedure."


def test_metadata_marks_a_skill_unavailable_when_its_dependency_is_missing() -> None:
    registry = SkillRegistry([skill("pdf", dependencies=("filesystem",))])

    metadata = registry.metadata("pdf")

    assert metadata.available is False
    assert metadata.unavailable_reason == "required skill filesystem is unavailable"


def test_resolve_uses_semver_precedence_for_prereleases_and_builds() -> None:
    registry = SkillRegistry([
        skill("pdf", version="1.0.0-rc.2"),
        skill("pdf", version="1.0.0-rc.10"),
        skill("pdf", version="1.0.0+build.7"),
    ])

    assert registry.resolve("pdf").version == "1.0.0+build.7"


def test_resolve_prefers_canonical_version_when_build_metadata_has_equal_precedence() -> None:
    registry = SkillRegistry([skill("pdf", version="1.0.0+build.7"), skill("pdf", version="1.0.0")])

    assert registry.resolve("pdf").version == "1.0.0"


def test_metadata_and_retrieval_exclude_transitively_unavailable_skills() -> None:
    registry = SkillRegistry([
        skill("pdf", dependencies=("document-tools",)),
        skill("document-tools", dependencies=("filesystem",)),
        skill("filesystem", requires_tools=("filesystem.read",)),
    ])

    assert registry.metadata("pdf").available is False
    assert registry.retrieve(
        RetrievalQuery("read a pdf", available_tools=()),
    ).items == ()


def test_metadata_and_retrieval_exclude_dependency_cycles() -> None:
    registry = SkillRegistry([
        skill("pdf", dependencies=("filesystem",)),
        skill("filesystem", dependencies=("pdf",)),
    ])

    assert registry.metadata("pdf").available is False
    assert registry.retrieve(RetrievalQuery("read a pdf")).items == ()


def test_metadata_and_retrieval_exclude_dependency_trees_beyond_load_limit() -> None:
    registry = SkillRegistry([
        skill("pdf", dependencies=("document-tools",)),
        skill("document-tools", dependencies=("filesystem",)),
        skill("filesystem"),
    ], maximum_dependency_depth=1)

    assert registry.metadata("pdf").available is False
    assert registry.retrieve(RetrievalQuery("read a pdf")).items == ()
    with pytest.raises(SkillDependencyDepthExceeded):
        registry.load("pdf")


def test_metadata_only_package_rejects_content_drift_when_loaded(tmp_path: Path) -> None:
    package = tmp_path / "pdf" / "SKILL.md"
    package.parent.mkdir()
    package.write_text("---\nname: pdf\ndescription: Read PDF files.\nversion: 1.0.0\n---\n# Trusted workflow\n", encoding="utf-8")
    registry = SkillRegistry([parse_skill_file(package, include_instructions=False)])
    package.write_text("---\nname: pdf\ndescription: Read PDF files.\nversion: 1.0.0\n---\n# Replaced workflow\n", encoding="utf-8")

    with pytest.raises(SkillUnavailable, match="content changed"):
        registry.load("pdf")


def test_metadata_only_package_rejects_frontmatter_drift_when_loaded(tmp_path: Path) -> None:
    package = tmp_path / "pdf" / "SKILL.md"
    package.parent.mkdir()
    package.write_text("---\nname: pdf\ndescription: Read PDF files.\nversion: 1.0.0\n---\n# Trusted workflow\n", encoding="utf-8")
    registry = SkillRegistry([parse_skill_file(package, include_instructions=False)])
    package.write_text("---\nname: pdf\ndescription: Export confidential files.\nversion: 1.0.0\n---\n# Trusted workflow\n", encoding="utf-8")

    with pytest.raises(SkillUnavailable, match="content changed"):
        registry.load("pdf")


def test_package_resources_are_confined_to_the_skill_resources_directory(tmp_path: Path) -> None:
    package = tmp_path / "resource-skill"
    resources = package / "resources"
    resources.mkdir(parents=True)
    (package / "SKILL.md").write_text("---\nname: Resource Skill\ndescription: Uses a reference.\nversion: 1.0.0\n---\n# Workflow", encoding="utf-8")
    (resources / "reference.md").write_text("reference body", encoding="utf-8")
    registry = SkillRegistry([parse_skill_file(package, include_instructions=False)])

    assert registry.read_resource("resource-skill", "resources/reference.md") == "reference body"
    with pytest.raises(SkillUnavailable):
        registry.read_resource("resource-skill", "../SKILL.md")


def test_parser_accepts_standard_yaml_flow_lists() -> None:
    skill = parse_skill_markdown(
        "---\nname: pdf\ndescription: Read PDF files.\nversion: 1.0.0\n"
        "tags: [pdf, documents]\ndependencies:\n  tools: [filesystem.read, filesystem.write]\n---\n# Workflow\n",
    )

    assert skill.tags == ("pdf", "documents")
    assert skill.required_tools == ("filesystem.read", "filesystem.write")


def test_parser_rejects_duplicate_or_unknown_frontmatter_keys() -> None:
    duplicate = "---\nname: pdf\nname: other\ndescription: Read PDF files.\nversion: 1.0.0\n---\n# Workflow\n"
    unknown_nested = "---\nname: pdf\ndescription: Read PDF files.\nversion: 1.0.0\ndependencies:\n  surprise: tool\n---\n# Workflow\n"

    with pytest.raises(SkillParseError, match="duplicate"):
        parse_skill_markdown(duplicate)
    with pytest.raises(SkillParseError, match="dependencies"):
        parse_skill_markdown(unknown_nested)
