import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import metadata
from agentos.persistence.postgres.skills import PostgresSkillLibraryService


def test_custom_skill_survives_a_new_library_service_instance() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    first = PostgresSkillLibraryService(engine)
    created = first.create({
        "user_id": "u1", "name": "Deploy Review", "description": "Review a deploy.", "version": "1.0.0",
        "tags": ["deploy"], "capabilities": ["review_deploy"],
        "when_to_use": ["before deployment"], "when_not_to_use": ["for a code-style request"],
        "requires_tools": ["read_file"], "dependencies": {"tools": ["run_command"]}, "instructions": "# Workflow",
    })

    reloaded = PostgresSkillLibraryService(engine)
    detail = reloaded.get({"user_id": "u1", "skill_id": created["id"]})

    assert detail["instructions"] == "# Workflow"
    assert detail["requires_tools"] == ["run_command", "read_file"]
    assert created["id"] in [item["id"] for item in reloaded.list({"user_id": "u1", "query": "deploy", "limit": 20})["items"]]


def test_postgres_skill_store_returns_detail_for_an_unavailable_skill() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)
    legacy = service._custom_skill({
        "user_id": "u1", "name": "PDF Extractor", "description": "Extract PDF data.",
        "version": "1.0.0", "requires_tools": ["extract_pdf_text"], "instructions": "# Workflow",
    }, skill_id="pdf-extractor")
    service._insert(legacy, user_id="u1")

    detail = service.get({"user_id": "u1", "skill_id": "pdf-extractor"})

    assert detail["available"] is False
    assert detail["instructions"] == "# Workflow"
    assert detail["requires_tools"] == ["extract_pdf_text"]


def test_postgres_skill_store_rejects_unavailable_tools_before_persisting() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)

    with pytest.raises(ValueError, match="requires unavailable tool 'extract_pdf_text'"):
        service.create({
            "user_id": "u1", "name": "PDF Extractor", "description": "Extract PDF data.",
            "version": "1.0.0", "requires_tools": ["extract_pdf_text"], "instructions": "# Workflow",
        })

    assert service.list({"user_id": "u1", "query": "pdf", "limit": 20})["items"] == []


def test_postgres_skill_store_rejects_missing_skill_dependencies_before_persisting() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)

    with pytest.raises(ValueError, match="dependency 'pdf-reading' is not installed"):
        service.create({
            "user_id": "u1", "name": "PDF Extractor", "description": "Extract PDF data.",
            "version": "1.0.0", "dependencies": {"skills": ["pdf-reading"]}, "instructions": "# Workflow",
        })

    assert service.list({"user_id": "u1", "query": "pdf", "limit": 20})["items"] == []


def test_loaded_skill_records_an_immutable_execution_snapshot() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)
    created = service.create({"user_id": "u1", "name": "Deploy", "description": "Deploy safely.", "version": "1.0.0", "tags": [], "instructions": "# Workflow"})
    loaded = service.registry_for("u1").load(created["id"])

    service.record_load(user_id="u1", execution_id="exe-1", agent_id="agent-1", loaded=loaded)
    snapshots = service.loads_for_execution(user_id="u1", execution_id="exe-1")

    assert snapshots[0]["version"] == "1.0.0"
    assert snapshots[0]["content_snapshot"] == "# Workflow"


def test_user_scope_can_override_a_builtin_identity_without_colliding() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)

    service.create({"user_id": "u1", "name": "Testing", "description": "A private testing process.", "version": "1.0.0", "tags": [], "instructions": "# Private workflow"})

    assert service.get({"user_id": "u1", "skill_id": "testing"})["instructions"] == "# Private workflow"


def test_postgres_skill_store_removes_an_old_version_and_keeps_execution_snapshots() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)
    created = service.create({"user_id": "u1", "name": "Cleanup", "description": "Clean safely.", "version": "1.0.0", "tags": [], "instructions": "# v1"})
    service.update({"user_id": "u1", "skill_id": created["id"], "description": "Clean safely.", "instructions": "# v2"})

    removed = service.remove_version({"user_id": "u1", "skill_id": created["id"], "version": "1.0.0"})

    assert removed["versions"] == ["1.0.1"]


def test_agent_can_switch_between_auto_discovery_and_pinned_skill_versions() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)

    assert service.agent_skills({"user_id": "u1", "agent_id": "a1"}) == {"mode": "auto", "items": []}
    pinned = service.set_agent_skills({"user_id": "u1", "agent_id": "a1", "mode": "pinned", "skill_ids": ["testing"]})

    assert pinned["mode"] == "pinned"
    assert pinned["items"][0]["id"] == "testing"
    assert service.agents_for_skill({"user_id": "u1", "skill_id": "testing"})["items"] == [{"agent_id": "a1", "mode": "pinned"}]
    assert service.set_agent_skills({"user_id": "u1", "agent_id": "a1", "mode": "auto", "skill_ids": []}) == {"mode": "auto", "items": []}
