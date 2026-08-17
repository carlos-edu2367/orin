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


def test_a_version_pin_argument_on_a_native_launcher_is_not_refused():
    # `uv` ships as a native Windows PE binary (like python/node/deno/bun) -
    # the OS never re-invokes it through cmd.exe, so a literal '<' in a
    # pinned dependency spec such as '--with mcp<2' (see
    # test_mcp_launch_pins_the_mcp_dependency in the plugins suite, which
    # requires exactly this pin syntax) cannot be reinterpreted as
    # redirection. Only npm's own cmd-shim launchers (npx) carry that risk on
    # Windows. Reproduces a live bug: installing obsidian-second-brain's
    # vault MCP server (command uv, args ...--with mcp<2...) was refused with
    # "the command line carries shell metacharacters" even after the
    # ${CLAUDE_PLUGIN_ROOT} substitution fix, because '<' was forbidden for
    # every launcher regardless of shim risk.
    StdioTransport(command="uv", args=("run", "--with", "mcp<2", "python", "server.py"), env={})


def test_a_percent_or_caret_argument_on_a_native_launcher_is_not_refused():
    StdioTransport(command="uv", args=("%SOME_VAR%",), env={})
    StdioTransport(command="node", args=("a^b",), env={})


def test_a_credential_value_carrying_a_shell_metacharacter_is_refused():
    # The proven attack: a secret value containing '&' combined with a '%VAR%'
    # placeholder argument lets cmd.exe expand-then-reparse into a second
    # command once an npx/uvx shim runs. Blocking it here means the malicious
    # value never reaches a process environment in the first place.
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("run",), env={"TOKEN": "abc&echo INJECTED>marker.txt&xyz"})


def test_open_forwards_the_temp_and_profile_variables_real_launchers_need(tmp_path):
    # Reproduces a live bug: obsidian-second-brain's vault MCP server
    # (`uv run --with mcp<2 python server.py`) launched fine after the
    # shell-metacharacter and ${CLAUDE_PLUGIN_ROOT} fixes, but exited
    # instantly with no output every time - StdioTransport.send() raised
    # "the MCP server closed before answering". Root cause: open() built the
    # child environment from only PATH/SystemRoot, and uv needs a writable
    # temp directory; without TEMP/TMP it falls back to a location under
    # C:\Windows a normal user cannot write to ("Acesso negado ... at path
    # C:\WINDOWS\.tmpXXXX"), so uv exited before ever reaching the server
    # script. Real launchers on Windows also expect USERPROFILE/APPDATA/
    # LOCALAPPDATA to be set (uv/npm/node cache and config locations).
    script = tmp_path / "env_probe.py"
    script.write_text(
        "import json, os, sys\n"
        "for line in sys.stdin:\n"
        "    frame = json.loads(line)\n"
        "    if 'id' not in frame:\n"
        "        continue\n"
        "    names = ('TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA') if os.name == 'nt' else ('HOME', 'TMPDIR')\n"
        "    present = {name: bool(os.environ.get(name)) for name in names}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': frame['id'], 'result': present}) + '\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, allow_any_command=True)
    transport.open()
    try:
        reply = transport.send({"jsonrpc": "2.0", "id": 1, "method": "probe"})
    finally:
        transport.close()
    assert reply is not None
    present = reply["result"]
    assert all(present.values()), f"expected every platform variable to be forwarded, got {present}"


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
