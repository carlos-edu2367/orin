from agentos.persistence.postgres.schema import metadata


def test_skill_schema_keeps_versions_associations_and_execution_snapshots_separate() -> None:
    expected = {"skills", "skill_versions", "agent_skills", "execution_skills"}

    assert expected <= set(metadata.tables)
    assert "content_digest" in metadata.tables["skill_versions"].c
    assert "content_snapshot" in metadata.tables["execution_skills"].c
    assert "skill_version_id" in metadata.tables["agent_skills"].c
