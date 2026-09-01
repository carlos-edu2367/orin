from sqlalchemy import create_engine

from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import metadata
from agentos.projects import PostgresProjectStore


def test_project_memory_never_leaks_to_another_project_or_standalone() -> None:
    """Dropping the project predicate would expose facts across isolated work."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    a = PostgresAgentMemoryStore(engine, "user", project_id="project-a")
    b = PostgresAgentMemoryStore(engine, "user", project_id="project-b")
    standalone = PostgresAgentMemoryStore(engine, "user")

    a.save("Project uses PostgreSQL.", ("architecture",))

    assert [item["fact"] for item in a.search("database PostgreSQL")] == ["Project uses PostgreSQL."]
    assert b.search("database PostgreSQL") == []
    assert standalone.search("database PostgreSQL") == []


def test_project_memory_keeps_user_memory_available_without_storing_project_fact_globally() -> None:
    """A project session needs relevant user facts but its own fact must stay scoped."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    user = PostgresAgentMemoryStore(engine, "user")
    project = PostgresAgentMemoryStore(engine, "user", project_id="project-a")
    user.save("User prefers concise answers.")
    project.save("Frontend uses React.")

    facts = {item["fact"] for item in project.recent()}
    assert facts == {"User prefers concise answers.", "Frontend uses React."}
    assert {item["fact"] for item in user.recent()} == {"User prefers concise answers."}


def test_project_memory_management_lists_only_its_scope_and_can_delete() -> None:
    """Project memory management must not list or delete a global user fact."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    project = PostgresProjectStore(engine).create(user_id="user", name="AgentOS", description=None)
    global_memory = PostgresAgentMemoryStore(engine, "user").save("User likes concise answers.")
    project_memory = PostgresAgentMemoryStore(engine, "user", project_id=project.project_id).save("Use FastAPI.")

    managed = PostgresProjectStore(engine).list_memories(project.project_id, "user")
    assert [item["memory_id"] for item in managed] == [project_memory["memory_id"]]
    assert PostgresProjectStore(engine).delete_memory(project.project_id, "user", str(global_memory["memory_id"])) is False
    assert PostgresProjectStore(engine).delete_memory(project.project_id, "user", str(project_memory["memory_id"])) is True


def test_updating_a_memory_rewrites_its_fact_and_returns_it():
    from datetime import UTC, datetime
    from sqlalchemy import insert
    from agentos.persistence.postgres.schema import agent_memories

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(agent_memories).values(
            memory_id="mem_1", user_id="user:1", conversation_id=None, scope_type="user",
            scope_id="user:1", project_id=None, source_message_id=None, source_execution_id=None,
            fact="o build é npm", tags=[], kind="operational", confidence=0.7, source="mechanical",
            hit_count=0, last_used_at=None, superseded_by=None, created_at=now, updated_at=now,
        ))
    store = PostgresProjectStore(engine)

    updated = store.update_memory(None, "user:1", "mem_1", "o build é pnpm", scope="user")

    assert updated["fact"] == "o build é pnpm"
    assert updated["memory_id"] == "mem_1"


def test_updating_a_memory_that_is_not_yours_returns_nothing():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    assert PostgresProjectStore(engine).update_memory(None, "user:1", "mem_nope", "x", scope="user") is None
