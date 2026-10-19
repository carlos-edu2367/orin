"""W5: MCP servers from files are connected once, cached, and join the turn's tools."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import agentos.mcp.file_servers as file_servers
from agentos.agentic.reminders import ReminderQueue
from agentos.mcp.file_config import parse_server
from agentos.mcp.file_servers import clear_discovery_cache, connect_file_servers, file_bundle
from agentos.mcp.models import McpToolDescriptor
from agentos.mcp.toolset import McpToolProvider, build_client
from agentos.settings.loader import EffectiveSettings
from agentos.settings.paths import orin_user_dir

TOOL = McpToolDescriptor("lookup", "Looks things up", {"type": "object", "properties": {}})


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_discovery_cache()
    yield
    clear_discovery_cache()


def _server(name: str = "docs", **raw):
    return parse_server(name, raw or {"command": "node", "args": ["srv.js"]}, scope="user", source="test", environ={}, cwd="C:/work")


def _connect(calls: list[str]):
    def connect(config, secrets):
        calls.append(config.slug)
        return "2025-06-18", (TOOL,)
    return connect


def test_servers_are_listed_once_and_cached() -> None:
    calls: list[str] = []
    bundles, failures = connect_file_servers([_server()], user_id="u1", connect=_connect(calls))
    assert failures == [] and [bundle[0].slug for bundle in bundles] == ["docs"] and bundles[0][1] == (TOOL,)
    config = bundles[0][0]
    assert config.origin == "user" and config.cwd == "C:/work" and config.server_id == "file:user:docs"
    again, _ = connect_file_servers([_server()], user_id="u1", connect=_connect(calls))
    assert calls == ["docs"] and again[0][1] == (TOOL,)


def test_a_failure_is_reported_without_its_secrets() -> None:
    server = parse_server(
        "api", {"type": "http", "url": "https://mcp.example.com/mcp?key=s3cret-key", "headers": {"Authorization": "Bearer tok-12345"}},
        scope="user", source="t", environ={},
    )

    def connect(config, secrets):
        assert secrets == {"header:Authorization": "Bearer tok-12345"}
        raise RuntimeError("401 for Bearer tok-12345 at https://mcp.example.com/mcp?key=s3cret-key")

    bundles, failures = connect_file_servers([server], user_id="u1", connect=connect)
    assert bundles == [] and failures[0][0] == "api"
    assert "tok-12345" not in failures[0][1] and "s3cret-key" not in failures[0][1] and "401" in failures[0][1]


def test_a_slow_server_does_not_hold_the_turn() -> None:
    gate = threading.Event()

    def connect(config, secrets):
        gate.wait(5)
        return "v", (TOOL,)

    started = time.monotonic()
    bundles, failures = connect_file_servers([_server()], user_id="u1", connect=connect, timeout=0.2)
    assert time.monotonic() - started < 3 and bundles == [] and "did not answer" in failures[0][1]
    gate.set()
    deadline = time.monotonic() + 5
    while not file_servers._cache and time.monotonic() < deadline:
        time.sleep(0.02)

    def not_called(config, secrets):
        raise AssertionError("the late answer should have been cached")

    later, _ = connect_file_servers([_server()], user_id="u1", connect=not_called, timeout=1)
    assert later and later[0][1] == (TOOL,)


def test_a_file_server_may_use_any_launcher_and_a_local_url() -> None:
    from agentos.mcp.transport_stdio import StdioTransportRefused

    config, _, secrets = file_bundle(_server("tool", command="my-tool", args=["--serve"]), user_id="u1")
    assert build_client(config, secrets)._transport._cwd == "C:/work"
    with pytest.raises(StdioTransportRefused):
        build_client(replace(config, origin="orin"), secrets)  # Orin's own servers keep the launcher list
    remote = parse_server("dev", {"type": "http", "url": "http://127.0.0.1:3000/mcp", "headers": {"X-Key": "abc"}}, scope="local", source="t", environ={})
    config, _, secrets = file_bundle(remote, user_id="u1")
    assert build_client(config, secrets)._transport._headers == {"X-Key": "abc"}
    with pytest.raises(Exception):
        build_client(replace(config, origin="orin"), secrets)  # a model-proposed server stays public-HTTPS only


def test_the_provider_takes_file_servers_and_drops_refused_ones() -> None:
    config, _, secrets = file_bundle(_server(), user_id="u1")
    provider = McpToolProvider([])
    provider.add_bundles([(config, (TOOL,), secrets)])
    assert [item.name for item in provider.definitions()] == ["mcp__docs__lookup"] and provider.server_slugs() == frozenset({"docs"})
    dropped = provider.retain(lambda item: item.slug != "docs")
    assert [item.slug for item in dropped] == ["docs"] and provider.definitions() == ()


def _turn_session(tmp_path: Path, *, local: bool = True):
    from tests.unit.agentic.test_disk_skills_session import _session

    session = _session(tmp_path, "hi")
    session.mcp_service = SimpleNamespace(allow_stdio=True) if local else None
    session._mcp_file_connect = _connect([])
    return session


def _user_servers(servers: dict) -> None:
    path = orin_user_dir() / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_a_turn_gets_the_tools_of_the_persons_servers(tmp_path: Path) -> None:
    _user_servers({"docs": {"command": "node", "args": ["srv.js"]}})
    session = _turn_session(tmp_path)
    runtime = session.build_runtime()
    assert session.mcp_provider is not None and session.mcp_provider.server_slugs() == frozenset({"docs"})
    assert "mcp__docs__lookup" in {item.name for item in session.mcp_provider.definitions()}
    assert runtime.toolset._mcp_provider is session.mcp_provider


def test_a_shared_orin_does_not_load_the_persons_servers(tmp_path: Path) -> None:
    _user_servers({"docs": {"command": "node", "args": ["srv.js"]}})
    session = _turn_session(tmp_path, local=False)
    session.build_runtime()
    assert session.mcp_provider is None


def test_the_model_hears_of_pending_and_failed_servers_and_denied_ones_leave(tmp_path: Path) -> None:
    _user_servers({"broken": {"type": "http", "url": "https://${NOT_SET_ANYWHERE_42}/mcp"}, "docs": {"command": "node"}})
    session = _turn_session(tmp_path)
    root = session.workspace.root
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"repo-tools": {"command": "node"}}}), encoding="utf-8")
    db_config, _, db_secrets = file_bundle(_server("legacy"), user_id="u1")
    session.mcp_provider = McpToolProvider([(replace(db_config, origin="orin", server_id="db-1"), (TOOL,), db_secrets)])
    session.effective_settings = EffectiveSettings({"deniedMcpServers": [{"serverName": "legacy"}]}, {}, (), (), True)
    session._reminders = ReminderQueue()
    session._load_file_mcp_servers(True)
    assert session.mcp_provider.server_slugs() == frozenset({"docs"})
    text = "\n".join(item.text for item in session._reminders.drain() if item.kind == "mcp")
    assert "repo-tools" in text and "not approved" in text
    assert "legacy" in text and "deniedMcpServers" in text
    assert "broken" in text and "NOT_SET_ANYWHERE_42 is not set" in text
