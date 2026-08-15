import sys
import time

import pytest

from agentos.mcp.transport_stdio import StdioTransport, StdioTransportError, StdioTransportRefused

ECHO_SERVER = """
import json, sys
for line in sys.stdin:
    frame = json.loads(line)
    if "id" not in frame:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {"echo": frame["method"]}}) + "\\n")
    sys.stdout.flush()
"""

SLEEPY_SERVER = """
import json, sys, time
for line in sys.stdin:
    frame = json.loads(line)
    if "id" not in frame:
        continue
    time.sleep(10)
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {}}) + "\\n")
    sys.stdout.flush()
"""


def test_a_command_outside_the_allowlist_is_refused():
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="curl", args=("https://example.com",), env={})


def test_a_command_carrying_shell_metacharacters_is_refused():
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("thing; rm -rf /",), env={})


def test_an_argument_carrying_a_percent_or_caret_is_refused():
    # On Windows, npx/uvx resolve to .cmd/.bat shims that the OS loader
    # re-invokes through cmd.exe even with shell=False, so a literal %VAR%
    # placeholder or a ^ escape in an argument is not inert.
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("%SOME_VAR%",), env={})
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("a^&b",), env={})


def test_a_credential_value_carrying_a_shell_metacharacter_is_refused():
    # The proven attack: a secret value containing '&' combined with a '%VAR%'
    # placeholder argument lets cmd.exe expand-then-reparse into a second
    # command once an npx/uvx shim runs. Blocking it here means the malicious
    # value never reaches a process environment in the first place.
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("run",), env={"TOKEN": "abc&echo INJECTED>marker.txt&xyz"})


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


def test_send_times_out_and_kills_a_server_that_never_answers(tmp_path):
    script = tmp_path / "sleepy_server.py"
    script.write_text(SLEEPY_SERVER, encoding="utf-8")
    transport = StdioTransport(
        command=sys.executable, args=(str(script),), env={}, allow_any_command=True, timeout=0.5,
    )
    transport.open()
    started = time.monotonic()
    try:
        with pytest.raises(StdioTransportError):
            transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    finally:
        transport.close()
    elapsed = time.monotonic() - started
    assert elapsed < 5, "send() should raise around the configured timeout, not wait for the 10s reply"
