import pytest

from agentos.skills.service import SkillLibraryService


def test_library_service_creates_lists_and_versions_a_user_skill() -> None:
    service = SkillLibraryService()
    created = service.create({
        "user_id": "u1", "name": "Internal Review", "description": "Review internal changes.",
        "version": "1.0.0", "tags": ["review"], "capabilities": ["review_change"],
        "when_to_use": ["before an internal release"], "when_not_to_use": ["for an external audit"],
        "requires_tools": ["read_file"], "dependencies": {"tools": ["run_command"]},
        "instructions": "# Workflow\n\nReview it.",
    })

    listed = service.list({"user_id": "u1", "query": "internal", "limit": 20})
    detail = service.get({"user_id": "u1", "skill_id": created["id"]})

    assert created["source"] == "custom"
    assert [item["id"] for item in listed["items"]] == [created["id"]]
    assert detail["instructions"].startswith("# Workflow")
    assert detail["requires_tools"] == ["run_command", "read_file"]


def test_library_service_returns_detail_for_a_skill_unavailable_to_the_runtime() -> None:
    service = SkillLibraryService(builtins=())
    legacy = service._custom_skill({
        "user_id": "u1", "name": "PDF Extractor", "description": "Extract PDF data.",
        "version": "1.0.0", "requires_tools": ["extract_pdf_text"], "instructions": "# Workflow",
    }, skill_id="pdf-extractor")
    service._custom["u1"] = [legacy]

    detail = service.get({"user_id": "u1", "skill_id": "pdf-extractor"})

    assert detail["available"] is False
    assert detail["instructions"] == "# Workflow"
    assert detail["requires_tools"] == ["extract_pdf_text"]


def test_library_service_rejects_unavailable_tools_before_persisting() -> None:
    service = SkillLibraryService(builtins=())

    with pytest.raises(ValueError, match="requires unavailable tool 'extract_pdf_text'"):
        service.create({
            "user_id": "u1", "name": "PDF Extractor", "description": "Extract PDF data.",
            "version": "1.0.0", "requires_tools": ["extract_pdf_text"], "instructions": "# Workflow",
        })

    assert service.list({"user_id": "u1", "query": "pdf", "limit": 20})["items"] == []


def test_library_service_rejects_missing_skill_dependencies_before_persisting() -> None:
    service = SkillLibraryService(builtins=())

    with pytest.raises(ValueError, match="dependency 'pdf-reading' is not installed"):
        service.create({
            "user_id": "u1", "name": "PDF Extractor", "description": "Extract PDF data.",
            "version": "1.0.0", "dependencies": {"skills": ["pdf-reading"]}, "instructions": "# Workflow",
        })

    assert service.list({"user_id": "u1", "query": "pdf", "limit": 20})["items"] == []


def test_library_service_removes_only_an_old_user_version() -> None:
    service = SkillLibraryService(builtins=())
    created = service.create({"user_id": "u1", "name": "Cleanup", "description": "Clean safely.", "version": "1.0.0", "instructions": "# v1"})
    service.update({"user_id": "u1", "skill_id": created["id"], "description": "Clean safely.", "instructions": "# v2"})

    removed = service.remove_version({"user_id": "u1", "skill_id": created["id"], "version": "1.0.0"})

    assert removed["versions"] == ["1.0.1"]
    try:
        service.remove_version({"user_id": "u1", "skill_id": created["id"], "version": "1.0.1"})
    except ValueError as error:
        assert "current" in str(error)
    else:
        raise AssertionError("the active version must remain installed")
