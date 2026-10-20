"""W6 (W5-D2): a stdio MCP server's own children end with it, even when it exits on its own."""
from __future__ import annotations

import os
import subprocess
import sys
import time

from agentos.mcp.transport_stdio import StdioTransport

PARENT = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
print("ready", flush=True)
for line in sys.stdin:
    pass
"""


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie of a process we did not start counts as gone
        with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
            return handle.read().split()[2] != "Z"
    except OSError:
        return True


def test_a_polite_servers_child_dies_when_the_server_closes(tmp_path) -> None:
    script = tmp_path / "server.py"
    script.write_text(PARENT, encoding="utf-8")
    marker = tmp_path / "child.pid"
    transport = StdioTransport(command=sys.executable, args=(str(script), str(marker)), env={}, allow_any_command=True)
    transport.open()
    assert transport._replies.get(timeout=30).strip() == b"ready"
    child = int(marker.read_text())
    assert _alive(child)
    transport.close()
    deadline = time.monotonic() + 10
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)
