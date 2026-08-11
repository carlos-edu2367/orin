from agentos.skills.service import SkillLibraryService


def test_library_service_creates_lists_and_versions_a_user_skill() -> None:
    service = SkillLibraryService()
    created = service.create({
        "user_id": "u1", "name": "Internal Review", "description": "Review internal changes.",
        "version": "1.0.0", "tags": ["review"], "instructions": "# Workflow\n\nReview it.",
    })

    listed = service.list({"user_id": "u1", "query": "internal", "limit": 20})
    detail = service.get({"user_id": "u1", "skill_id": created["id"]})

    assert created["source"] == "custom"
    assert [item["id"] for item in listed["items"]] == [created["id"]]
    assert detail["instructions"].startswith("# Workflow")
