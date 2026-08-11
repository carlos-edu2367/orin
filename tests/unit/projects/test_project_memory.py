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
