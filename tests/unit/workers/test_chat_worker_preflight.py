import pytest
from sqlalchemy import create_engine, select

from agentos.accounts.store import UserStore
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.conversations.chat import PostgresChatStore
from agentos.installation import orin_paths, reset_cached_paths
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import conversation_dispatches, metadata
from agentos.workers.chat import ChatWorker

SERVER = InstanceCapabilities.for_mode(RuntimeMode.SERVER)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    reset_cached_paths()
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))
    users = UserStore(engine)
    admin = users.create(username="carla", password="a long password", role="admin")
    member = users.create(username="bruno", password="a long password")
    yield engine, store, users, admin, member
    reset_cached_paths()


def _never(turn):
    raise AssertionError("a refused turn must not build a runtime")


def _outcome(engine, turn_id):
    with engine.connect() as connection:
        row = connection.execute(select(conversation_dispatches.c.state, conversation_dispatches.c.last_error).where(conversation_dispatches.c.turn_id == turn_id)).one()
    return tuple(row)


def test_a_turn_from_a_deactivated_profile_never_runs(world):
    engine, store, users, _, member = world
    receipt = store.create(user_id=member.user_id, message="oi", provider="openrouter", model_id="m", idempotency_key="k1")
    users.update(member.user_id, active=False)
    ChatWorker(store, runtime_factory=_never, capabilities=SERVER).run(receipt.turn_id)
    assert _outcome(engine, receipt.turn_id) == ("failed", "owner_inactive")


def test_a_project_bound_outside_the_profile_area_is_refused(world, tmp_path):
    engine, store, _, admin, _ = world
    receipt = store.create(user_id=admin.user_id, message="oi", provider="openrouter", model_id="m", idempotency_key="k2")
    PostgresLocalWorkspaceStore(engine).set_root(receipt.conversation_id, admin.user_id, str(tmp_path / "host"))
    ChatWorker(store, runtime_factory=_never, capabilities=SERVER).run(receipt.turn_id)
    assert _outcome(engine, receipt.turn_id) == ("failed", "workspace_unavailable")


def test_preflight_accepts_a_folder_inside_the_area(world):
    engine, store, _, admin, _ = world
    inside = orin_paths().user_files(admin.user_id) / "proj"
    inside.mkdir(parents=True)
    receipt = store.create(user_id=admin.user_id, message="oi", provider="openrouter", model_id="m", idempotency_key="k3")
    PostgresLocalWorkspaceStore(engine).set_root(receipt.conversation_id, admin.user_id, str(inside))
    worker = ChatWorker(store, runtime_factory=_never, capabilities=SERVER)
    turn = store.claim(receipt.turn_id)
    assert worker._preflight_refusal(turn) is None


def test_local_mode_has_no_preflight(world, tmp_path):
    engine, store, _, admin, _ = world
    worker = ChatWorker(store, runtime_factory=_never, capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL))
    receipt = store.create(user_id="someone-without-account", message="oi", provider="openrouter", model_id="m", idempotency_key="k4")
    PostgresLocalWorkspaceStore(engine).set_root(receipt.conversation_id, "someone-without-account", str(tmp_path / "anywhere"))
    assert worker._preflight_refusal(store.claim(receipt.turn_id)) is None
