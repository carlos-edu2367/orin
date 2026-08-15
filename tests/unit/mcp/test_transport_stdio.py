import sys

import pytest

from agentos.mcp.transport_stdio import StdioTransport, StdioTransportRefused

ECHO_SERVER = """
import json, sys
for line in sys.stdin:
    frame = json.loads(line)
    if "id" not in frame:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {"echo": frame["method"]}}) + "\\n")
    sys.stdout.flush()
"""


def test_a_command_outside_the_allowlist_is_refused():
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="curl", args=("https://example.com",), env={})


def test_a_command_carrying_shell_metacharacters_is_refused():
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("thing; rm -rf /",), env={})


def test_the_transport_round_trips_a_frame(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER, encoding="utf-8")
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, allow_any_command=True)
    transport.open()
    try:
        assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"}) == {
            "jsonrpc": "2.0", "id": 1, "result": {"echo": "ping"},
        }
    finally:
        transport.close()


def test_close_is_idempotent(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER, encoding="utf-8")
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, allow_any_command=True)
    transport.open()
    transport.close()
    transport.close()
