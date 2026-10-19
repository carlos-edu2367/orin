"""W4 review fixes for WebFetch: every hop is validated and connected to the address that was validated (R15), the
body is bounded in size and time and cancelled between chunks, the page is wrapped in a per-call delimiter, and an
unanswered page carries the untrusted notice (R16). No test contacts a real host: names resolve through a stub, and
connections are recorded instead of made."""
from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.web import fetch as fetch_module
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.permissions import ToolPermissionBroker

PUBLIC = "93.184.216.34"


class _Resolver:
    """Stub DNS: names under .test resolve to the address given per name, in order; nothing else is looked up."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = {name: list(values) for name, values in answers.items()}
        self.lookups: list[str] = []

    def __call__(self, host, port, *args, **kwargs):
        if not isinstance(host, str) or not host.endswith(".test"):
            raise socket.gaierror("only .test names are resolved in tests")
        self.lookups.append(host)
        values = self.answers.get(host, [PUBLIC])
        address = values.pop(0) if len(values) > 1 else values[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]


@pytest.fixture()
def resolver(monkeypatch: pytest.MonkeyPatch) -> _Resolver:
    stub = _Resolver({})
    monkeypatch.setattr(socket, "getaddrinfo", stub)
    return stub


@pytest.fixture()
def connections(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Every TCP connection the fetch would open; none is made."""
    made: list[tuple[str, int]] = []

    def record(address, *args, **kwargs):
        made.append((str(address[0]), int(address[1])))
        raise OSError("connections are recorded, not made, in tests")

    monkeypatch.setattr(socket, "create_connection", record)
    return made


def _toolset(tmp_path: Path, client: httpx.Client | None = None, **options) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_fetch_r15"), http_client=client, **options)


def _recording_client(routes: dict[str, httpx.Response]) -> tuple[httpx.Client, list[str]]:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(200, text="page", headers={"content-type": "text/plain"}))

    return httpx.Client(transport=httpx.MockTransport(handler)), requested


# R15: the address that was checked is the address that is connected to.

def test_a_redirect_to_the_shared_address_range_is_refused(tmp_path: Path, resolver: _Resolver) -> None:
    client, requested = _recording_client({
        "https://allowed.test/to-cgnat": httpx.Response(302, headers={"location": "http://100.100.100.200/latest/meta-data/"}),
    })
    outcome = _toolset(tmp_path, client).invoke("WebFetch", {"url": "https://allowed.test/to-cgnat", "prompt": "what?"})

    assert outcome.status != "succeeded"
    assert requested == ["https://allowed.test/to-cgnat"]  # the shared-range address was never requested


def test_a_redirect_to_a_host_a_fetch_rule_denies_is_refused(tmp_path: Path, resolver: _Resolver) -> None:
    broker = ToolPermissionBroker(
        ConversationWorkspace(tmp_path, "chat_fetch_r15_rules"), trusted=True,
        cli_settings={"permissions": {"allow": ["WebFetch(domain:allowed.test)"], "deny": ["WebFetch(domain:blocked.test)"]}},
    )
    client, requested = _recording_client({
        "https://allowed.test/to-blocked": httpx.Response(302, headers={"location": "https://blocked.test/secret"}),
    })
    outcome = _toolset(tmp_path, client, permission_broker=broker, permission_mode="accept_edits").invoke(
        "WebFetch", {"url": "https://allowed.test/to-blocked", "prompt": "what?"},
    )

    assert outcome.status != "succeeded"
    assert requested == ["https://allowed.test/to-blocked"]  # the first hop was fetched, the denied redirect never was


def test_the_production_client_connects_to_the_address_it_validated(tmp_path: Path, resolver: _Resolver, connections) -> None:
    resolver.answers["rebind.test"] = [PUBLIC, "127.0.0.1"]  # the second answer is loopback
    outcome = _toolset(tmp_path).invoke("WebFetch", {"url": "http://rebind.test/", "prompt": "token?"})

    assert outcome.status != "succeeded"
    assert resolver.lookups.count("rebind.test") == 1  # resolved once for the whole fetch
    assert connections == [(PUBLIC, 80)]  # connected to the checked address, never to loopback


def test_a_name_is_resolved_in_one_spelling_and_connected_by_address(tmp_path: Path, resolver: _Resolver, connections) -> None:
    outcome = _toolset(tmp_path).invoke("WebFetch", {"url": "http://faß.test/", "prompt": "what?"})

    assert outcome.status != "succeeded"
    assert resolver.lookups == ["xn--fa-hia.test"]
    assert connections == [(PUBLIC, 80)]


@pytest.mark.parametrize("url", [
    "http://intranet/", "http://printer.lan/", "http://box.internal/", "http://mac.local/", "http://dev.localhost/",
    "http://10.0.0.1/", "http://100.100.100.200/", "http://[fd00::1]/",
])
def test_local_and_single_label_names_are_refused_without_a_lookup(tmp_path: Path, resolver: _Resolver, connections, url: str) -> None:
    outcome = _toolset(tmp_path).invoke("WebFetch", {"url": url, "prompt": "what?"})

    assert outcome.status != "succeeded"
    assert resolver.lookups == [] and connections == []


def test_a_name_that_does_not_resolve_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connections) -> None:
    def unresolvable(host, *args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", unresolvable)
    outcome = _toolset(tmp_path).invoke("WebFetch", {"url": "http://missing.test/", "prompt": "what?"})

    assert outcome.status != "succeeded" and connections == []
