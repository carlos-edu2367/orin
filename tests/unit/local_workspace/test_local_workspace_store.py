from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresLocalWorkspaceStore:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    return PostgresLocalWorkspaceStore(engine)


def test_set_root_is_an_upsert_and_read_is_scoped_to_the_owner() -> None:
    """Reading another user's root would leak where their files live."""
    store = _store()
    store.set_root("workspace:project_a", "owner", "/tmp/um")
    store.set_root("workspace:project_a", "owner", "/tmp/dois")

    assert store.root_for("workspace:project_a", "owner") == "/tmp/dois"
    assert store.root_for("workspace:project_a", "other") is None
    assert store.root_for("workspace:unknown", "owner") is None


def test_clear_root_removes_it_and_is_idempotent() -> None:
    store = _store()
    store.set_root("chat_a", "owner", "/tmp/um")

    assert store.clear_root("chat_a", "owner") is True
    assert store.root_for("chat_a", "owner") is None
    assert store.clear_root("chat_a", "owner") is False
