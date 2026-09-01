from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import agent_memories, metadata


def test_agent_memories_carries_the_learning_columns():
    names = set(agent_memories.c.keys())
    assert {"kind", "confidence", "source", "hit_count", "last_used_at", "superseded_by"} <= names


def test_the_same_fact_can_exist_in_two_different_projects():
    """The old constraint was (user_id, fact), which made this raise."""
    from datetime import UTC, datetime
    from sqlalchemy import insert, select

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    row = {
        "user_id": "user:1", "scope_type": "project", "scope_id": "p", "fact": "o build é pnpm",
        "tags": [], "created_at": now, "updated_at": now, "kind": "operational",
        "confidence": 0.7, "source": "mechanical", "hit_count": 0,
    }
    with engine.begin() as connection:
        connection.execute(insert(agent_memories).values(memory_id="m1", project_id="project:a", **row))
        connection.execute(insert(agent_memories).values(memory_id="m2", project_id="project:b", **row))
    with engine.connect() as connection:
        assert len(connection.execute(select(agent_memories)).mappings().all()) == 2
