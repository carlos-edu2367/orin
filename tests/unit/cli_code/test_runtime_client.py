"""W6 Task 4: `orin code` finds (or starts) the local Orin and talks to it with the run token."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agentos.cli_code import runtime
from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.runtime import OrinClient, connect_local, find_or_start_runtime
from agentos.installation.api_token import issue_api_token
from agentos.installation.paths import OrinPaths
from agentos.launcher.state import InstanceState


@pytest.fixture
def paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


STATE = InstanceState(pid=1, port=48123, url="http://127.0.0.1:48123", version="9", profile="development", started_at="now")


def _client(handler) -> OrinClient:
    return OrinClient("http://127.0.0.1:48123", "secret-token", http=httpx.Client(base_url="http://127.0.0.1:48123", transport=httpx.MockTransport(handler)))


def test_requests_carry_the_token_and_mutations_an_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    assert client.get("/v1/instance") == {"ok": True}
    client.post("/v1/conversations", {"message": "hi"})
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "idempotency-key" not in seen[0].headers
    assert seen[1].headers["idempotency-key"].startswith("cli-")
    assert "secret-token" not in repr(client)


def test_an_api_refusal_is_a_runtime_error_without_the_body() -> None:
    client = _client(lambda request: httpx.Response(409, json={"error": {"code": "workspace_risk_acknowledgement_required", "detail": "x"}}))
    with pytest.raises(CliRuntimeError, match="409: workspace_risk_acknowledgement_required"):
        client.post("/v1/conversations", {})


def test_events_are_read_from_the_sse_stream_and_heartbeats_keep_the_cursor() -> None:
    body = (
        'id: 1\nevent: turn.started\ndata: {"event_type": "turn.started", "cursor": "c1"}\n\n'
        'event: heartbeat\ndata: {"cursor": "c1"}\n\n'
        'id: 2\nevent: assistant.delta\ndata: {"event_type": "assistant.delta", "payload": {"content": "hé"}, "cursor": "c2"}\n\n'
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("after")))
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})

    client = _client(handler)
    received: list[dict] = []
    for event in client.events("chat_x", stop=lambda: len(received) >= 3):
        received.append(event)
    assert [item["event_type"] for item in received] == ["turn.started", "heartbeat", "assistant.delta"]
    assert received[2]["payload"]["content"] == "hé"
    assert calls == ["0"]


def test_a_running_healthy_instance_is_used(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "running_instance", lambda _: STATE)
    monkeypatch.setattr(runtime, "_healthy", lambda _: True)
    assert find_or_start_runtime(paths, spawn=lambda _: pytest.fail("must not start a second Orin")) is STATE


def test_a_stopped_orin_is_started_and_waited_for(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    states = iter([None, None, STATE])
    monkeypatch.setattr(runtime, "running_instance", lambda _: next(states))
    monkeypatch.setattr(runtime, "_healthy", lambda _: True)
    spawned: list[OrinPaths] = []
    assert find_or_start_runtime(paths, spawn=spawned.append, sleep=lambda _: None) is STATE
    assert spawned == [paths]


def test_without_start_a_stopped_orin_is_an_error(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "running_instance", lambda _: None)
    with pytest.raises(CliRuntimeError, match="not running"):
        find_or_start_runtime(paths, start=False)


def test_an_orin_that_never_comes_up_times_out(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "running_instance", lambda _: None)
    ticks = iter(range(0, 1000, 10))
    with pytest.raises(CliRuntimeError, match="did not start within 30 s"):
        find_or_start_runtime(paths, timeout=30, spawn=lambda _: None, clock=lambda: next(ticks), sleep=lambda _: None)


def _patch_instance(monkeypatch: pytest.MonkeyPatch, info: dict) -> None:
    monkeypatch.setattr(runtime, "find_or_start_runtime", lambda *_, **__: STATE)
    monkeypatch.setattr(OrinClient, "get", lambda self, path, **params: info)


LOCAL = {"mode": "local", "capabilities": {"host_folders": True}, "credential": "cli_token", "version": "9"}


def test_connect_returns_a_client_for_a_local_orin_that_accepts_the_token(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    issue_api_token(paths.run)
    _patch_instance(monkeypatch, LOCAL)
    client = connect_local(paths)
    assert client.base_url == STATE.url
    client.close()


@pytest.mark.parametrize(("info", "message"), [
    ({**LOCAL, "mode": "server"}, "local Orin only"),
    ({**LOCAL, "capabilities": {"host_folders": False}}, "local Orin only"),
    ({**LOCAL, "credential": "loopback"}, "did not accept"),
    ([], "local Orin only"),
])
def test_connect_refuses_what_is_not_this_persons_local_orin(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch, info, message: str) -> None:
    issue_api_token(paths.run)
    _patch_instance(monkeypatch, info)
    with pytest.raises(CliRuntimeError, match=message):
        connect_local(paths)


def test_connect_without_a_token_says_how_to_fix_it(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_instance(monkeypatch, LOCAL)
    with pytest.raises(CliRuntimeError, match="orin restart"):
        connect_local(paths)


def test_a_server_environment_is_refused_before_anything_runs(paths: OrinPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_MODE", "server")
    monkeypatch.setattr(runtime, "find_or_start_runtime", lambda *_, **__: pytest.fail("nothing may start"))
    with pytest.raises(CliRuntimeError, match="ORIN_MODE=server"):
        connect_local(paths)


def test_the_runtime_is_started_with_the_orin_binary_beside_a_frozen_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.cli_code import runtime

    copy = tmp_path / "orin-code.exe"
    real = tmp_path / "orin.exe"
    copy.write_bytes(b"")
    real.write_bytes(b"")

    class Profile:
        def launcher_command(self) -> tuple[str, ...]:
            return (str(copy),)

    monkeypatch.setattr(runtime, "runtime_profile", lambda: Profile())
    assert runtime._launcher_command() == [str(real)]
