from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from agentos.persistence.postgres.schema import metadata, workspace_roots


def test_workspace_roots_stores_one_root_per_workspace() -> None:
    """The effective workspace id is the key; a second row for it must fail."""
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(workspace_roots).values(workspace_id="chat_a", user_id="owner", root_path="/tmp/one", created_at=now, updated_at=now))
    with engine.connect() as connection:
        assert connection.execute(select(workspace_roots.c.root_path)).scalar_one() == "/tmp/one"
    try:
        with engine.begin() as connection:
            connection.execute(insert(workspace_roots).values(workspace_id="chat_a", user_id="owner", root_path="/tmp/two", created_at=now, updated_at=now))
    except IntegrityError:
        return
    raise AssertionError("workspace_id must be unique")
