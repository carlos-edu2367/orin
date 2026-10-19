"""W5 review fixes for the MCP transports (docs/superpowers/reviews/2026-10-19-w5-review.md).

Each call waits for its answer, and a server's own requests restart that wait. These tests send a call to a server that
keeps asking, and check that the call still ends (W5-R9). Every call runs in a worker thread with a join deadline, so a
transport that still hangs fails the test instead of blocking the run.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from agentos.mcp.transport_sse import SseTransport, SseTransportError
from agentos.mcp.transport_stdio import StdioTransport, StdioTransportError
from agentos.mcp.transport_ws import WebSocketTransport, WebSocketTransportError

CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}
MAX_SERVER_REQUESTS_PER_CALL = 100  # the finding's cap

# Pings every INTERVAL seconds (argv[1]) and answers nothing: the call can only end by the transport's own limits.
PINGING_SERVER = """
import json, sys, threading, time
interval = float(sys.argv[1])
def pinger():
    n = 0
    while True:
        n += 1
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": f"p{n}", "method": "ping"}) + "\\n")
        sys.stdout.flush()
        time.sleep(interval)
started = False
for line in sys.stdin:
    if not started:
        started = True
        threading.Thread(target=pinger, daemon=True).start()
"""

# Pings a few times, then answers the call: the ordinary case, which the per-wait restart must keep working.
POLITE_SERVER = """
import json, sys, time
for line in sys.stdin:
    frame = json.loads(line)
    if "id" not in frame:
        continue
    for n in range(5):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": f"p{n}", "method": "ping"}) + "\\n")
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {"done": True}}) + "\\n")
    sys.stdout.flush()
"""


def _send_in_worker(transport, frame: dict) -> tuple[threading.Thread, list]:
    outcome: list = []

    def run() -> None:
        try:
            outcome.append(transport.send(frame))
        except Exception as error:  # noqa: BLE001 - the test reads what the call ended with
            outcome.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, outcome


def _answer_every_ping(message: dict):
    return {} if message.get("method") == "ping" else None


# W5-R9: stdio --------------------------------------------------------------------------------------------------------


def test_stdio_polite_server_still_gets_its_answer_after_several_pings(tmp_path: Path) -> None:
    script = tmp_path / "polite.py"
    script.write_text(POLITE_SERVER, encoding="utf-8")
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, timeout=0.5, allow_any_command=True)
    transport.on_message = _answer_every_ping
    transport.open()
    try:
        assert transport.send(CALL) == {"jsonrpc": "2.0", "id": 1, "result": {"done": True}}
    finally:
        transport.close()


def test_stdio_server_pinging_forever_is_cut_at_the_call_ceiling(tmp_path: Path) -> None:
    script = tmp_path / "pinger.py"
    script.write_text(PINGING_SERVER, encoding="utf-8")
    # 10 x timeout = 3 s of ceiling; pings every 0.05 s never leave the per-wait deadline expired.
    transport = StdioTransport(command=sys.executable, args=(str(script), "0.05"), env={}, timeout=0.3, allow_any_command=True)
    transport.on_message = _answer_every_ping
    transport.open()
    started = time.monotonic()
    try:
        worker, outcome = _send_in_worker(transport, CALL)
        worker.join(20)
        elapsed = time.monotonic() - started
        assert not worker.is_alive(), "the call still waits for the answer after 20 s"
        assert isinstance(outcome[0], StdioTransportError), outcome
        assert "longer than 3s" in str(outcome[0])
        assert 2.5 <= elapsed <= 10, elapsed
        with pytest.raises(StdioTransportError, match="not running"):
            transport.send(CALL)  # the server was terminated, like after an ordinary timeout
    finally:
        transport.close()


def test_stdio_server_that_sends_too_many_requests_in_one_call_is_refused(tmp_path: Path) -> None:
    script = tmp_path / "pinger.py"
    script.write_text(PINGING_SERVER, encoding="utf-8")
    # Pings every 5 ms: the request cap is reached well before the 5 s ceiling of timeout=0.5.
    transport = StdioTransport(command=sys.executable, args=(str(script), "0.005"), env={}, timeout=0.5, allow_any_command=True)
    transport.on_message = _answer_every_ping
    transport.open()
    started = time.monotonic()
    try:
        worker, outcome = _send_in_worker(transport, CALL)
        worker.join(20)
        elapsed = time.monotonic() - started
        assert not worker.is_alive(), "the call still waits for the answer after 20 s"
        assert isinstance(outcome[0], StdioTransportError), outcome
        assert f"more than {MAX_SERVER_REQUESTS_PER_CALL} requests" in str(outcome[0])
        assert elapsed < 4.5, elapsed
    finally:
        transport.close()


# W5-R9: the HTTP+SSE transport ----------------------------------------------------------------------------------------


def _pinging_sse_client(stop: threading.Event, interval: float) -> httpx.Client:
    def stream():
        yield b"event: endpoint\ndata: /messages\n\n"
        n = 0
        while not stop.is_set():
            n += 1
            yield f'event: message\ndata: {{"jsonrpc":"2.0","id":"p{n}","method":"ping"}}\n\n'.encode()
            time.sleep(interval)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())
        return httpx.Response(202)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sse_server_pinging_forever_is_cut_at_the_call_ceiling() -> None:
    stop = threading.Event()
    transport = SseTransport(url="http://127.0.0.1:9/sse", client=_pinging_sse_client(stop, 0.05), allow_local=True, timeout=0.3)
    transport.on_message = _answer_every_ping
    transport.open()
    try:
        worker, outcome = _send_in_worker(transport, CALL)
        worker.join(20)
        assert not worker.is_alive(), "the call still waits for the answer after 20 s"
        assert isinstance(outcome[0], SseTransportError), outcome
        assert "longer than 3s" in str(outcome[0])
    finally:
        stop.set()
        transport.close()


def test_sse_server_that_sends_too_many_requests_in_one_call_is_refused() -> None:
    stop = threading.Event()
    transport = SseTransport(url="http://127.0.0.1:9/sse", client=_pinging_sse_client(stop, 0.005), allow_local=True, timeout=0.5)
    transport.on_message = _answer_every_ping
    transport.open()
    try:
        worker, outcome = _send_in_worker(transport, CALL)
        worker.join(20)
        assert not worker.is_alive(), "the call still waits for the answer after 20 s"
        assert isinstance(outcome[0], SseTransportError), outcome
        assert f"more than {MAX_SERVER_REQUESTS_PER_CALL} requests" in str(outcome[0])
    finally:
        stop.set()
        transport.close()


# W5-R9: WebSocket ----------------------------------------------------------------------------------------------------


class _PingingSocket:
    """A connection whose server pings every ``interval`` seconds and never answers the call."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._count = 0

    def send(self, raw: str) -> None:
        return None

    def recv(self, timeout: float | None = None) -> str:
        time.sleep(self._interval)
        self._count += 1
        return json.dumps({"jsonrpc": "2.0", "id": f"p{self._count}", "method": "ping"})

    def close(self) -> None:
        return None


def test_websocket_server_pinging_forever_is_cut_at_the_call_ceiling() -> None:
    socket = _PingingSocket(0.05)
    transport = WebSocketTransport(url="ws://127.0.0.1:9/mcp", allow_local=True, timeout=0.3,
                                   connect=lambda url, **options: socket)
    transport.on_message = _answer_every_ping
    transport.open()
    try:
        worker, outcome = _send_in_worker(transport, CALL)
        worker.join(20)
        assert not worker.is_alive(), "the call still waits for the answer after 20 s"
        assert isinstance(outcome[0], WebSocketTransportError), outcome
        assert "longer than 3s" in str(outcome[0])
    finally:
        transport.close()


def test_websocket_server_that_sends_too_many_requests_in_one_call_is_refused() -> None:
    socket = _PingingSocket(0.005)
    transport = WebSocketTransport(url="ws://127.0.0.1:9/mcp", allow_local=True, timeout=0.5,
                                   connect=lambda url, **options: socket)
    transport.on_message = _answer_every_ping
    transport.open()
    try:
        worker, outcome = _send_in_worker(transport, CALL)
        worker.join(20)
        assert not worker.is_alive(), "the call still waits for the answer after 20 s"
        assert isinstance(outcome[0], WebSocketTransportError), outcome
        assert f"more than {MAX_SERVER_REQUESTS_PER_CALL} requests" in str(outcome[0])
    finally:
        transport.close()
