from sqlalchemy import create_engine

from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import metadata
from agentos.workers.chat import ChatWorker


def _store(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))


def test_a_server_worker_closes_hooks_and_the_terminal(tmp_path):
    worker = ChatWorker(_store(tmp_path), capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER))
    assert worker._capabilities.shell is False
    assert worker._hook_engine.enabled is False


def test_a_worker_defaults_to_the_process_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_MODE", "server")
    assert ChatWorker(_store(tmp_path))._capabilities.shell is False
    monkeypatch.setenv("ORIN_MODE", "local")
    assert ChatWorker(_store(tmp_path))._capabilities.shell is True


def test_the_worker_plugin_service_refuses_host_paths_on_a_server(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    from agentos.installation import reset_cached_paths
    reset_cached_paths()
    store = _store(tmp_path)
    server = ChatWorker(store, capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER))
    local = ChatWorker(store, capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL))
    assert server._plugin_service(store._engine, skill_library=None, mcp_service=None).fetcher.remote_only is True
    assert local._plugin_service(store._engine, skill_library=None, mcp_service=None).fetcher.remote_only is False
    reset_cached_paths()
