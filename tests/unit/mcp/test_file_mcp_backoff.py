"""Failing MCP servers from files back off (1, 2, 4, 8 min, cap 10) instead of being retried every turn."""
from __future__ import annotations

import pytest

from agentos.mcp import file_servers
from agentos.mcp.file_config import parse_server
from agentos.mcp.file_servers import clear_discovery_cache, connect_file_servers, format_failure_notes
from agentos.mcp.models import McpToolDescriptor

TOOL = McpToolDescriptor("lookup", "Looks things up", {"type": "object", "properties": {}})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh():
    clear_discovery_cache()
    yield
    clear_discovery_cache()


def _server(name: str = "unity-mcp"):
    return parse_server(name, {"command": "node", "args": ["srv.js"]}, scope="user", source="~/.claude.json", environ={}, cwd="C:/work")


class Flaky:
    def __init__(self) -> None:
        self.calls = 0
        self.up = False

    def __call__(self, config, secrets):
        self.calls += 1
        if not self.up:
            raise ConnectionRefusedError("[WinError 10061] recusou ativamente")
        return "v", (TOOL,)


def _run(connect, clock, recovered=None):
    return connect_file_servers([_server()], user_id="u1", connect=connect, clock=clock, recovered=recovered)


def test_a_refused_server_is_skipped_during_its_backoff_and_retried_after() -> None:
    clock, connect = Clock(), Flaky()
    _, failures = _run(connect, clock)
    assert connect.calls == 1 and "10061" in failures[0][1]
    clock.now += 30
    _, failures = _run(connect, clock)
    assert connect.calls == 1  # no connection attempt while skipped
    assert failures[0][1].startswith("unavailable (next retry in ")
    clock.now += 31  # 61 s since the failure
    _run(connect, clock)
    assert connect.calls == 2


def test_the_interval_doubles_and_is_capped() -> None:
    assert [file_servers.backoff_seconds(n) for n in range(1, 8)] == [60, 120, 240, 480, 600, 600, 600]
    clock, connect = Clock(), Flaky()
    for expected in (60, 120, 240):
        _run(connect, clock)
        clock.now += expected - 1
        calls = connect.calls
        _run(connect, clock)
        assert connect.calls == calls
        clock.now += 2


def test_success_resets_the_backoff_and_reports_recovery_once() -> None:
    clock, connect = Clock(), Flaky()
    _run(connect, clock)
    clock.now += 61
    connect.up = True
    recovered: list[str] = []
    bundles, failures = _run(connect, clock, recovered)
    assert bundles and failures == [] and recovered == ["unity-mcp"]
    recovered.clear()
    _run(connect, clock, recovered)
    assert recovered == []
    connect.up = False
    file_servers.clear_discovery_cache()
    _, failures = _run(connect, clock)
    assert "10061" in failures[0][1]  # a fresh failure starts at the shortest interval again


def test_only_the_first_failure_carries_the_reason() -> None:
    clock, connect = Clock(), Flaky()
    _, first = _run(connect, clock)
    clock.now += 61
    _, second = _run(connect, clock)  # retried, failed again
    assert "10061" in first[0][1] and second[0][1].startswith("unavailable (next retry in ")
    clock.now += 10
    _, third = _run(connect, clock)
    assert third[0][1].startswith("unavailable (next retry in 2 min") or third[0][1].startswith("unavailable (next retry in 1 min")


def test_a_changed_configuration_is_tried_at_once() -> None:
    clock, connect = Clock(), Flaky()
    _run(connect, clock)
    other = parse_server("unity-mcp", {"command": "node", "args": ["other.js"]}, scope="user", source="~/.claude.json", environ={}, cwd="C:/work")
    connect_file_servers([other], user_id="u1", connect=connect, clock=clock)
    assert connect.calls == 2


def test_servers_are_keyed_by_file_and_name() -> None:
    clock, connect = Clock(), Flaky()
    _run(connect, clock)
    same_name_elsewhere = parse_server("unity-mcp", {"command": "node", "args": ["srv.js"]}, scope="project", source=".mcp.json", environ={}, cwd="C:/work")
    connect_file_servers([same_name_elsewhere], user_id="u1", connect=connect, clock=clock)
    assert connect.calls == 2


def test_a_timeout_counts_once_even_if_the_late_attempt_fails() -> None:
    import threading

    clock = Clock()
    gate = threading.Event()

    def slow_then_fail(config, secrets):
        gate.wait(5)
        raise ConnectionRefusedError("refused")

    _, failures = connect_file_servers([_server()], user_id="u1", connect=slow_then_fail, timeout=0.1, clock=clock)
    assert "did not answer" in failures[0][1]
    gate.set()
    import time

    deadline = time.monotonic() + 5
    entry = file_servers._backoff[file_servers._identity(_server())]
    while entry.late and time.monotonic() < deadline:
        time.sleep(0.02)
    assert entry.failures == 1


def test_failure_notes_group_the_servers_in_backoff_in_one_line() -> None:
    lines = format_failure_notes([
        ("unity-mcp", "unavailable (next retry in 3 min)"), ("diffusion", "unavailable (next retry in 3 min)"),
        ("new", "could not connect: boom"),
    ])
    assert lines == ["- new: could not connect: boom", "- unity-mcp, diffusion: unavailable (next retry in 3 min)"]
